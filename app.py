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
import gc

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

# Set up FFmpeg environment variables
if os.environ.get('RAILWAY_STATIC_URL'):
    AudioSegment.converter = "ffmpeg"
    AudioSegment.ffmpeg = "ffmpeg"
    AudioSegment.ffprobe = "ffprobe"
else:
    # Local development setup
    FFMPEG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg")
    if not os.path.exists(FFMPEG_PATH):
        os.makedirs(FFMPEG_PATH, exist_ok=True)

    possible_ffmpeg_paths = [
        r"C:\Program Files\ffmpeg\bin",
        r"C:\ffmpeg\bin",
        FFMPEG_PATH,
        os.environ.get("PATH", "").split(os.pathsep)
    ]

    ffmpeg_found = False
    for path in possible_ffmpeg_paths:
        if isinstance(path, str) and os.path.exists(os.path.join(path, "ffmpeg.exe")):
            os.environ["PATH"] += os.pathsep + path
            AudioSegment.converter = os.path.join(path, "ffmpeg.exe")
            AudioSegment.ffmpeg = os.path.join(path, "ffmpeg.exe")
            AudioSegment.ffprobe = os.path.join(path, "ffprobe.exe")
            ffmpeg_found = True
            break

    if not ffmpeg_found:
        logger.error("FFmpeg not found. Please install FFmpeg and make sure it's in your PATH")

def cleanup_temp_files(*files):
    """Clean up temporary files"""
    for file in files:
        try:
            if file and os.path.exists(file):
                os.remove(file)
                logger.info(f"Cleaned up temporary file: {file}")
        except Exception as e:
            logger.error(f"Error cleaning up file {file}: {str(e)}")

def process_audio_in_chunks(input_path, output_path, chunk_duration=30000):
    """Process audio in chunks to reduce memory usage"""
    try:
        # Load audio file info without loading the entire file
        audio = AudioSegment.from_file(input_path)
        total_duration = len(audio)
        logger.info(f"Total audio duration: {total_duration}ms")

        # Process in chunks
        processed_chunks = []
        for start_time in range(0, total_duration, chunk_duration):
            end_time = min(start_time + chunk_duration, total_duration)
            logger.info(f"Processing chunk: {start_time}ms to {end_time}ms")
            
            # Extract and process chunk
            chunk = audio[start_time:end_time]
            fast_chunk = chunk.speedup(playback_speed=1.15)
            processed_chunks.append(fast_chunk)
            
            # Clear memory
            del chunk
            gc.collect()

        # Combine chunks
        logger.info("Combining processed chunks")
        final_audio = sum(processed_chunks, AudioSegment.empty())
        
        # Export with optimized settings
        logger.info("Exporting final audio")
        final_audio.export(
            output_path,
            format=os.path.splitext(output_path)[1][1:],
            parameters=["-q:a", "0"]
        )
        
        # Clear memory
        del audio, final_audio, processed_chunks
        gc.collect()
        
        return True
    except Exception as e:
        logger.error(f"Error in process_audio_in_chunks: {str(e)}")
        logger.error(traceback.format_exc())
        return False

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

        # Process audio in chunks
        if process_audio_in_chunks(temp_input, temp_output):
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
