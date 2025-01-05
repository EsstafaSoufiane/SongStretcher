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
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size

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

def process_audio_with_ffmpeg(input_path, output_path):
    """Process audio using direct FFmpeg command"""
    try:
        ffmpeg_path = get_ffmpeg_path()
        logger.info(f"Using FFmpeg at: {ffmpeg_path}")
        
        # Calculate the speed factor (1.15x = 100/115)
        atempo = "1.15"
        
        # Construct FFmpeg command
        cmd = [
            ffmpeg_path,
            "-i", input_path,
            "-filter:a", f"atempo={atempo}",
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

@app.route('/process', methods=['POST'])
def process_audio():
    temp_input = None
    temp_output = None
    
    try:
        logger.info("Processing new audio request")
        if 'file' not in request.files:
            logger.error("No file part in request")
            return jsonify({'error': 'No file part'}), 400
        
        file = request.files['file']
        if file.filename == '':
            logger.error("No selected file")
            return jsonify({'error': 'No selected file'}), 400

        # Validate file extension
        file_ext = os.path.splitext(file.filename)[1].lower()
        if file_ext not in ['.mp3', '.wav']:
            logger.error(f"Invalid file extension: {file_ext}")
            return jsonify({'error': 'Only MP3 and WAV files are allowed'}), 400

        # Generate unique filenames
        unique_id = str(uuid.uuid4())
        filename = secure_filename(file.filename)
        temp_input = str(TEMP_DIR / f"input_{unique_id}{file_ext}")
        temp_output = str(TEMP_DIR / f"output_{unique_id}{file_ext}")

        logger.info(f"Processing file: {filename}")
        file.save(temp_input)
        logger.info(f"File saved to: {temp_input}")

        # Process audio using FFmpeg
        if process_audio_with_ffmpeg(temp_input, temp_output):
            logger.info("Audio processing completed successfully")
            return send_file(
                temp_output,
                as_attachment=True,
                download_name=f"fast_{filename}"
            )
        else:
            return jsonify({'error': 'Failed to process audio'}), 500

    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': f'Unexpected error: {str(e)}'}), 500

    finally:
        # Clean up temporary files
        cleanup_temp_files(temp_input, temp_output)

if __name__ == '__main__':
    app.run(debug=True)
