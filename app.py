from flask import Flask, render_template, request, send_file, jsonify, Response, stream_with_context
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
import gc
import psutil

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

def cleanup_temp_files(*files):
    """Clean up temporary files and force garbage collection"""
    for file in files:
        try:
            if file and os.path.exists(file):
                os.remove(file)
                logger.info(f"Cleaned up temporary file: {file}")
        except Exception as e:
            logger.error(f"Error cleaning up file {file}: {str(e)}")
    
    # Force garbage collection
    gc.collect()

def check_memory_usage():
    """Check current memory usage and log if it's high"""
    process = psutil.Process(os.getpid())
    memory_usage = process.memory_info().rss / 1024 / 1024  # Convert to MB
    logger.info(f"Current memory usage: {memory_usage:.2f} MB")
    return memory_usage

def process_audio_with_ffmpeg(input_path, output_path, speed=1.15, volume=1.0):
    """Process audio using direct FFmpeg command with memory optimization"""
    try:
        initial_memory = check_memory_usage()
        ffmpeg_path = get_ffmpeg_path()
        logger.info(f"Using FFmpeg at: {ffmpeg_path}")
        
        # Construct FFmpeg command with speed and volume filters
        filter_str = f"atempo={speed},volume={volume}"
        
        # Add progress handling and memory optimization to FFmpeg command
        cmd = [
            ffmpeg_path,
            "-i", input_path,
            "-filter:a", filter_str,
            "-y",  # Overwrite output file if it exists
            "-progress", "pipe:1",  # Output progress to stdout
            "-loglevel", "info",
            "-max_memory", "100M",  # Limit FFmpeg memory usage
            "-compression_level", "6",  # Balance between speed and memory usage
            output_path
        ]
        
        logger.info(f"Running FFmpeg command: {' '.join(cmd)}")
        
        # Run FFmpeg command with progress monitoring
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            bufsize=1
        )
        
        # Monitor the process and memory usage
        while True:
            if process.poll() is not None:
                break
                
            output = process.stdout.readline()
            if output:
                logger.info(f"FFmpeg progress: {output.strip()}")
            
            # Check memory usage periodically
            current_memory = check_memory_usage()
            if current_memory > initial_memory + 500:  # If memory increased by 500MB
                logger.warning(f"High memory usage detected: {current_memory:.2f} MB")
        
        if process.returncode != 0:
            stderr = process.stderr.read()
            logger.error(f"FFmpeg error: {stderr}")
            return False
            
        logger.info("FFmpeg processing completed successfully")
        final_memory = check_memory_usage()
        logger.info(f"Memory usage change: {final_memory - initial_memory:.2f} MB")
        return True
        
    except Exception as e:
        logger.error(f"Error in process_audio_with_ffmpeg: {str(e)}")
        logger.error(traceback.format_exc())
        return False
    finally:
        # Force garbage collection after processing
        gc.collect()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/process-audio', methods=['POST'])
def process_audio():
    input_path = None
    output_path = None
    
    try:
        initial_memory = check_memory_usage()
        
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
        logger.info(f"Memory usage after file save: {check_memory_usage():.2f} MB")

        # Check audio duration
        duration = get_audio_duration(input_path)
        if duration is not None and duration > 12 * 60:  # 12 minutes in seconds
            cleanup_temp_files(input_path)
            return jsonify({'error': 'Audio file duration exceeds 12 minutes limit'}), 400

        def generate():
            try:
                # Process the audio file
                success = process_audio_with_ffmpeg(input_path, output_path, speed, volume)
                
                if not success:
                    cleanup_temp_files(input_path, output_path)
                    yield jsonify({'error': 'Failed to process audio'}).get_data(as_text=True)
                    return

                # Stream the file in chunks
                chunk_size = 8192  # 8KB chunks to conserve memory
                with open(output_path, 'rb') as f:
                    while True:
                        chunk = f.read(chunk_size)
                        if not chunk:
                            break
                        yield chunk
                        
                        # Check memory usage periodically
                        current_memory = check_memory_usage()
                        if current_memory > initial_memory + 500:  # If memory increased by 500MB
                            logger.warning(f"High memory usage during streaming: {current_memory:.2f} MB")

            finally:
                # Clean up temp files after streaming
                cleanup_temp_files(input_path, output_path)
                gc.collect()  # Force garbage collection after processing

        return Response(
            stream_with_context(generate()),
            mimetype='audio/mpeg',
            headers={
                'Content-Disposition': f'attachment; filename={output_filename}'
            }
        )

    except Exception as e:
        logger.error(f"Error in process_audio: {str(e)}")
        logger.error(traceback.format_exc())
        if input_path or output_path:
            cleanup_temp_files(input_path, output_path)
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
