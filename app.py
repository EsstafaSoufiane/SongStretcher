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
from rq import Queue
from redis import Redis
import time

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

# Set up Redis and RQ
redis_url = os.getenv('REDIS_URL')
if not redis_url:
    # Fallback to synchronous processing if Redis is not available
    logger.warning("Redis URL not found, falling back to synchronous processing")
    USE_REDIS = False
else:
    try:
        redis_conn = Redis.from_url(redis_url)
        redis_conn.ping()  # Test connection
        queue = Queue(connection=redis_conn)
        USE_REDIS = True
        logger.info("Successfully connected to Redis")
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {str(e)}")
        USE_REDIS = False

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

def process_audio_with_ffmpeg(input_path, output_path, speed, volume):
    """Process audio file using FFmpeg with cartoon/anime voice effect"""
    try:
        logger.info(f"Current memory usage: {check_memory_usage():.2f} MB")
        
        ffmpeg_path = 'ffmpeg'
        logger.info(f"Using FFmpeg at: {ffmpeg_path}")
        
        # Complex filter for cartoon/anime voice effect:
        # 1. Speed adjustment (atempo)
        # 2. Pitch shifting (asetrate, aresample)
        # 3. Echo/reverb effect (aecho)
        # 4. Volume adjustment
        filter_complex = (
            f"asetrate=44100*1.3,aresample=44100,"  # Pitch up
            f"atempo={speed},"  # Speed adjustment
            f"aecho=0.8:0.8:40|50|70:0.4|0.3|0.2,"  # Echo effect
            f"volume={volume},"  # Volume adjustment
            f"highpass=f=300,lowpass=f=4000"  # EQ adjustment
        )
        
        command = [
            ffmpeg_path,
            '-i', input_path,
            '-filter_complex', filter_complex,
            '-y',
            '-progress', 'pipe:1',
            '-loglevel', 'info',
            '-threads', '2',
            '-compression_level', '6',
            output_path
        ]
        
        logger.info(f"Running FFmpeg command: {' '.join(command)}")
        
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )
        
        stdout, stderr = process.communicate()
        
        if process.returncode != 0:
            logger.error(f"FFmpeg error: {stderr}")
            return False
            
        logger.info(f"Current memory usage after processing: {check_memory_usage():.2f} MB")
        return True
        
    except Exception as e:
        logger.error(f"Error in process_audio_with_ffmpeg: {str(e)}")
        return False
    finally:
        gc.collect()

def process_audio_job(input_path, output_path, speed, volume):
    """Background job for processing audio"""
    try:
        success = process_audio_with_ffmpeg(input_path, output_path, speed, volume)
        if success:
            return {'status': 'completed', 'output_path': output_path}
        return {'status': 'failed', 'error': 'Processing failed'}
    except Exception as e:
        logger.error(f"Error in process_audio_job: {str(e)}")
        return {'status': 'failed', 'error': str(e)}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/process-audio', methods=['POST'])
def process_audio():
    input_path = None
    output_path = None
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
        job_id = str(uuid.uuid4())
        input_path = os.path.join(app.config['UPLOAD_FOLDER'], f"input_{job_id}_{filename}")
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], f"output_{job_id}_{filename}")

        # Save uploaded file
        file.save(input_path)

        # Check audio duration
        duration = get_audio_duration(input_path)
        if duration is not None and duration > 12 * 60:  # 12 minutes in seconds
            cleanup_temp_files(input_path)
            return jsonify({'error': 'Audio file duration exceeds 12 minutes limit'}), 400

        if USE_REDIS:
            # Queue the processing job
            job = queue.enqueue(
                process_audio_job,
                args=(input_path, output_path, speed, volume),
                job_timeout='10m',  # 10 minutes timeout
                result_ttl=300  # Keep result for 5 minutes
            )

            return jsonify({
                'status': 'processing',
                'job_id': job.id,
                'message': 'Audio processing started',
                'queue_enabled': True
            }), 202
        else:
            # Process synchronously if Redis is not available
            success = process_audio_with_ffmpeg(input_path, output_path, speed, volume)
            if not success:
                cleanup_temp_files(input_path, output_path)
                return jsonify({'error': 'Failed to process audio'}), 500

            return jsonify({
                'status': 'completed',
                'message': 'Audio processed successfully',
                'queue_enabled': False,
                'download_url': f'/download/direct/{os.path.basename(output_path)}'
            })

    except Exception as e:
        logger.error(f"Error in process_audio: {str(e)}")
        if input_path:
            cleanup_temp_files(input_path)
        if output_path and os.path.exists(output_path):
            cleanup_temp_files(output_path)
        return jsonify({'error': str(e)}), 500

@app.route('/status/<job_id>', methods=['GET'])
def get_status(job_id):
    """Check the status of a processing job"""
    try:
        job = queue.fetch_job(job_id)
        if job is None:
            return jsonify({'status': 'not_found'}), 404

        if job.is_failed:
            return jsonify({
                'status': 'failed',
                'error': str(job.exc_info)
            }), 500

        if job.is_finished:
            result = job.result
            if result['status'] == 'completed':
                return jsonify({
                    'status': 'completed',
                    'download_url': f'/download/{job_id}'
                })
            return jsonify({'status': 'failed', 'error': result.get('error', 'Unknown error')})

        # Job is still processing
        return jsonify({
            'status': 'processing',
            'position': job.get_position(),
            'progress': job.meta.get('progress', 0)
        })

    except Exception as e:
        logger.error(f"Error checking job status: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/download/<job_id>', methods=['GET'])
def download_file(job_id):
    """Download the processed file"""
    try:
        job = queue.fetch_job(job_id)
        if job is None or not job.is_finished:
            return jsonify({'error': 'File not found'}), 404

        result = job.result
        if result['status'] != 'completed':
            return jsonify({'error': 'Processing failed'}), 500

        output_path = result['output_path']
        if not os.path.exists(output_path):
            return jsonify({'error': 'File not found'}), 404

        def generate():
            try:
                with open(output_path, 'rb') as f:
                    while True:
                        chunk = f.read(8192)
                        if not chunk:
                            break
                        yield chunk
            finally:
                cleanup_temp_files(output_path)

        filename = os.path.basename(output_path)
        return Response(
            generate(),
            mimetype='audio/mpeg',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )

    except Exception as e:
        logger.error(f"Error downloading file: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/download/direct/<filename>')
def download_direct(filename):
    """Direct download for synchronous processing"""
    try:
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if not os.path.exists(output_path):
            return jsonify({'error': 'File not found'}), 404

        def generate():
            try:
                with open(output_path, 'rb') as f:
                    while True:
                        chunk = f.read(8192)
                        if not chunk:
                            break
                        yield chunk
            finally:
                cleanup_temp_files(output_path)

        return Response(
            generate(),
            mimetype='audio/mpeg',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )

    except Exception as e:
        logger.error(f"Error downloading file: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
