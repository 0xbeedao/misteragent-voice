import sounddevice as sd
import numpy as np
from multiprocessing import Process, Pipe
import time
from collections import deque
import wave
import threading
import pvporcupine
import os
from dotenv import load_dotenv

# Configuration
BUFFER_DURATION = 60  # Buffer duration in seconds
SAMPLE_RATE = 44100   # Sample rate (samples per second)
NUM_CHANNELS = 1      # Mono audio
BUFFER_SIZE = BUFFER_DURATION * SAMPLE_RATE

def audio_callback(indata, buffer):
    """Callback function to receive audio data."""
    buffer.extend(indata)

def stream_audio(pipe):
    """Stream audio to a buffer continuously."""
    audio_buffer = deque(maxlen=BUFFER_SIZE)

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=NUM_CHANNELS, callback=lambda indata, frames, time, status: audio_callback(indata, audio_buffer)):
        print("Streaming audio...")
        while True:
            # Check for commands in the pipe
            if pipe.poll():  # Check if there is a message in the pipe
                command = pipe.recv()  # Receive command from the pipe
                if command == "report":
                    # Convert the deque to a NumPy array and send back
                    buffer_data = np.array(audio_buffer)
                    pipe.send(buffer_data.tolist())  # Send buffer back
                elif command == "stop":
                    print("Stopping audio stream.")
                    break
            time.sleep(0.1)  # Prevent busy waiting

def listen_for_wake_word(pipe):
    """Listen for the wake word and interact with the audio stream."""
    wake_word = os.getenv("PICOVOICE_KEYWORD")
    if wake_word is None:
     raise ValueError("PICOVOICE_KEYWORD is not set")
    access_key = os.getenv("PICOVOICE_ACCESS_KEY")
    if access_key is None:
      raise ValueError("PICOVOICE_ACCESS_KEY is not set")
    porcupine = pvporcupine.create(
        access_key = access_key,
        keyword_paths = [wake_word]
    )

    # Use a microphone as input for Porcupine
    mic = sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=512, dtype=np.int16)
    mic.start()

    print("Listening for wake word...")
    while True:
        pcm = mic.read(512)  # Read audio data
        pcm = np.frombuffer(pcm, dtype=np.int16)

        # Check for the wake word
        if porcupine.process(pcm) >= 0:
            print("Wake word detected! Stopping audio stream...")
            pipe.send("stop")  # Send stop command to audio stream
            time.sleep(1)  # Allow time for audio stream to stop

            # Wait for audio buffer and save it
            buffer_data = pipe.recv()
            save_audio_to_file(buffer_data)

            # Restart listening for wake word
            print("Restarting wake word detection...")
            continue

def save_audio_to_file(buffer_data):
    """Save the audio buffer to a timestamped WAV file."""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"audio_{timestamp}.wav"

    with wave.open(filename, 'wb') as wf:
        wf.setnchannels(NUM_CHANNELS)
        wf.setsampwidth(2)  # 16-bit audio
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(np.array(buffer_data, dtype=np.int16).tobytes())

    print(f"Audio saved to {filename}")


if __name__ == "__main__":
    load_dotenv()
  
    print("Starting wakeword listener...")
    print("PICOVOICE_KEYWORD: ", os.getenv("PICOVOICE_KEYWORD"))
    parent_conn, child_conn = Pipe()  # Create a pipe for communication
    audio_process = Process(target=stream_audio, args=(child_conn,))
    audio_process.start()

    # Start the wake word listener in a separate thread
    wake_word_thread = threading.Thread(target=listen_for_wake_word, args=(parent_conn,))
    wake_word_thread.start()

    try:
        while True:
            command = input("Enter command (report/stop): ")
            if command == "report":
                parent_conn.send(command)  # Send report command
                buffer_data = parent_conn.recv()  # Wait for the buffer data
                print(f"Received buffer data of length: {len(buffer_data)}")
            elif command == "stop":
                parent_conn.send(command)  # Send stop command
                break
    finally:
        audio_process.join()  # Ensure the audio process is cleaned up
        wake_word_thread.join()  # Clean up the wake word thread
        print("Audio process and wake word detection terminated.")