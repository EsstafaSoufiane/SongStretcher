from flask import Flask, render_template, request, send_file
from pydub import AudioSegment
import os
from werkzeug.utils import secure_filename
import tempfile

app = Flask(__name__)
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
        print("FFmpeg not found. Please install FFmpeg and make sure it's in your PATH")

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
        print(f"Error processing audio: {str(e)}")
        raise

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/process', methods=['POST'])
def process():
    if 'file' not in request.files:
        return 'No file uploaded', 400
    
    file = request.files['file']
    if file.filename == '':
        return 'No file selected', 400
    
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in ['.mp3', '.wav']:
        return 'Only MP3 and WAV files are allowed', 400

    # Create a unique filename to avoid conflicts
    filename = secure_filename(file.filename)
    temp_filename = f"{os.path.splitext(filename)[0]}_{os.urandom(4).hex()}{file_ext}"
    temp_path = os.path.join(app.config['UPLOAD_FOLDER'], temp_filename)
    
    try:
        file.save(temp_path)
        output_path = process_audio(temp_path, 1.15)  # Fixed speed up factor
        
        return send_file(
            output_path,
            as_attachment=True,
            download_name=f"speedup_{filename}"
        )
    except Exception as e:
        return f'Error processing file: {str(e)}', 500
    finally:
        # Clean up temporary files
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass  # Ignore cleanup errors

if __name__ == '__main__':
    app.run(debug=True)
