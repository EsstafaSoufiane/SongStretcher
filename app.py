from flask import Flask, render_template, request, send_file, jsonify
from pydub import AudioSegment
import os
from werkzeug.utils import secure_filename
import tempfile
from flask_cors import CORS
import logging
import traceback
import uuid
from pathlib import Path
import subprocess
import shutil
import re

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, resources={
    r"/*": {
        "origins": "*",
        "methods": ["GET", "POST"],
        "allow_headers": ["Content-Type"]
    }
})

# Set up temporary directory
TEMP_DIR = Path("/app/temp")
if not TEMP_DIR.exists():
    TEMP_DIR.mkdir(parents=True)

app.config['UPLOAD_FOLDER'] = str(TEMP_DIR)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max file size

def get_ffmpeg_path():
    """Get the appropriate FFmpeg path based on environment"""
    if os.environ.get('RAILWAY_STATIC_URL'):
        return "ffmpeg"
    
    # Local development setup
    possible_paths = [
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
    ]
    
    for path in possible_paths:
        if os.path.exists(path):
            return path
    
    return "ffmpeg"  # Default to system PATH

def get_audio_duration(file_path):
    """Get the duration of an audio file in seconds using FFmpeg"""
    try:
        ffmpeg_path = get_ffmpeg_path()
        cmd = [
            ffmpeg_path,
            "-i", file_path,
            "-hide_banner"
        ]
        
        # Run FFmpeg command to get file info
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )
        
        # FFmpeg prints file info to stderr
        stdout, stderr = process.communicate()
        
        # Find duration in output
        duration_match = re.search(r"Duration: (\d{2}):(\d{2}):(\d{2})", stderr)
        if duration_match:
            hours, minutes, seconds = map(int, duration_match.groups())
            total_seconds = hours * 3600 + minutes * 60 + seconds
            return total_seconds
        return None
    except Exception as e:
        logger.error(f"Error getting audio duration: {str(e)}")
        return None

def process_audio_with_ffmpeg(input_path, output_path, speed=1.15, volume=1.0):
    """Process audio using direct FFmpeg command"""
    try:
        ffmpeg_path = get_ffmpeg_path()
        logger.info(f"Using FFmpeg at: {ffmpeg_path}")
        
        # Construct FFmpeg command with speed and volume filters
        filter_str = f"atempo={speed},volume={volume}"
        
        # Construct FFmpeg command
        cmd = [
            ffmpeg_path,
            "-i", input_path,
            "-filter:a", filter_str,
            "-y",  # Overwrite output file if it exists
            "-loglevel", "info",
            output_path
        ]
        
        logger.info(f"Running FFmpeg command: {' '.join(cmd)}")
        
        # Run FFmpeg command
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )
        
        # Monitor the process
        stdout, stderr = process.communicate()
        
        if process.returncode != 0:
            logger.error(f"FFmpeg error: {stderr}")
            return False
            
        logger.info("FFmpeg processing completed successfully")
        return True
        
    except Exception as e:
        logger.error(f"Error in process_audio_with_ffmpeg: {str(e)}")
        logger.error(traceback.format_exc())
        return False

def cleanup_temp_files(*files):
    """Clean up temporary files"""
    for file in files:
        try:
            if file and os.path.exists(file):
                os.remove(file)
                logger.info(f"Cleaned up temporary file: {file}")
        except Exception as e:
            logger.error(f"Error cleaning up file {file}: {str(e)}")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/process-audio', methods=['POST'])
def process_audio():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400

        # Get speed and volume parameters from request
        speed = float(request.form.get('speed', 1.15))
        volume = float(request.form.get('volume', 1.0))

        # Validate parameters
        if not (0.5 <= speed <= 2.0):
            return jsonify({'error': 'Speed must be between 0.5 and 2.0'}), 400
        if not (0.0 <= volume <= 2.0):
            return jsonify({'error': 'Volume must be between 0.0 and 2.0'}), 400

        # Generate unique filenames
        filename = secure_filename(file.filename)
        input_path = os.path.join(app.config['UPLOAD_FOLDER'], f"input_{uuid.uuid4()}_{filename}")
        output_filename = f"processed_{filename}"
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], f"output_{uuid.uuid4()}_{filename}")

        # Save uploaded file
        file.save(input_path)

        # Check audio duration
        duration = get_audio_duration(input_path)
        if duration is not None and duration > 12 * 60:  # 12 minutes in seconds
            cleanup_temp_files(input_path)
            return jsonify({'error': 'Audio file duration exceeds 12 minutes limit'}), 400

        # Process the audio file
        success = process_audio_with_ffmpeg(input_path, output_path, speed, volume)

        if not success:
            cleanup_temp_files(input_path, output_path)
            return jsonify({'error': 'Failed to process audio'}), 500

        # Send the processed file
        response = send_file(
            output_path,
            as_attachment=True,
            download_name=output_filename,
            mimetype='audio/mpeg'
        )

        # Clean up temp files after sending
        @response.call_on_close
        def cleanup():
            cleanup_temp_files(input_path, output_path)

        return response

    except Exception as e:
        logger.error(f"Error in process_audio: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
