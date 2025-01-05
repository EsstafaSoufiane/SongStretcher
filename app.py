from flask import Flask, render_template, request, send_file, jsonify
from pydub import AudioSegment
import os
from werkzeug.utils import secure_filename
import tempfile
from flask_cors import CORS
import logging
import traceback

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, resources={
    r"/*": {  # Allow all routes since we're using the same domain
        "origins": "*",  # In production, you should specify your domain
        "methods": ["GET", "POST"],
        "allow_headers": ["Content-Type"]
    }
})

app.config['UPLOAD_FOLDER'] = tempfile.gettempdir()
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max file size

# Set up FFmpeg environment variables
if os.environ.get('RAILWAY_STATIC_URL'):
    # We're on Railway with Docker
    AudioSegment.converter = "ffmpeg"
    AudioSegment.ffmpeg = "ffmpeg"
    AudioSegment.ffprobe = "ffprobe"
else:
    # Local development setup
    FFMPEG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg")
    if not os.path.exists(FFMPEG_PATH):
        os.makedirs(FFMPEG_PATH, exist_ok=True)

    # Try multiple common FFmpeg locations
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

def process_audio(file_path, speed_factor):
    try:
        # Detect file type from extension
        file_ext = os.path.splitext(file_path)[1].lower()
        if file_ext == '.mp3':
            audio = AudioSegment.from_mp3(file_path)
        elif file_ext == '.wav':
            audio = AudioSegment.from_wav(file_path)
        else:
            raise ValueError("Unsupported file format")

        modified_audio = audio._spawn(audio.raw_data, overrides={"frame_rate": int(audio.frame_rate * speed_factor)})
        
        output_path = os.path.join(
            app.config['UPLOAD_FOLDER'],
            f"speedup_{os.path.basename(file_path)}"
        )
        # Export in the same format as input
        modified_audio.export(output_path, format=file_ext[1:])
        return output_path
    except Exception as e:
        logger.error(f"Error processing audio: {str(e)}")
        logger.error(traceback.format_exc())
        raise

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/process', methods=['POST'])
def process_audio():
    try:
        logger.info("Processing new audio request")
        if 'file' not in request.files:
            logger.error("No file part in request")
            return jsonify({'error': 'No file part'}), 400
        
        file = request.files['file']
        if file.filename == '':
            logger.error("No selected file")
            return jsonify({'error': 'No selected file'}), 400

        if file:
            filename = secure_filename(file.filename)
            logger.info(f"Processing file: {filename}")
            
            # Save uploaded file
            temp_input = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(temp_input)
            logger.info(f"File saved to: {temp_input}")

            try:
                # Load and process audio
                audio = AudioSegment.from_file(temp_input)
                logger.info("Audio file loaded successfully")
                
                # Speed up the audio (1.15x)
                fast_audio = audio.speedup(playback_speed=1.15)
                logger.info("Audio speed adjustment completed")

                # Save the processed audio
                output_filename = f"fast_{filename}"
                temp_output = os.path.join(app.config['UPLOAD_FOLDER'], output_filename)
                fast_audio.export(temp_output, format=os.path.splitext(filename)[1][1:])
                logger.info(f"Processed audio saved to: {temp_output}")

                # Send the file
                return send_file(
                    temp_output,
                    as_attachment=True,
                    download_name=output_filename
                )
            except Exception as e:
                logger.error(f"Error processing audio: {str(e)}")
                logger.error(traceback.format_exc())
                return jsonify({'error': f'Error processing audio: {str(e)}'}), 500
            finally:
                # Clean up temporary files
                try:
                    if os.path.exists(temp_input):
                        os.remove(temp_input)
                    if os.path.exists(temp_output):
                        os.remove(temp_output)
                except Exception as e:
                    logger.error(f"Error cleaning up temporary files: {str(e)}")

    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': f'Unexpected error: {str(e)}'}), 500

if __name__ == '__main__':
    app.run(debug=True)
