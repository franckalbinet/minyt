"""Fetching transcript"""

# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/00_core.ipynb.

# %% auto 0
__all__ = ['gemini_api_key', 'download_audio', 'detect_silence', 'parse_silence_ends', 'find_split_points', 'get_audio_duration',
           'get_mime_type', 'split_audio', 'transcribe_audio']

# %% ../nbs/00_core.ipynb 2
import os
import re
from pathlib import Path
from fastcore.all import *
from tqdm import tqdm   
import subprocess
import logging
from rich import print

from google import genai
from google.genai import types
import asyncio

import ffmpeg

# %% ../nbs/00_core.ipynb 3
gemini_api_key = os.getenv("GEMINI_API_KEY")

# %% ../nbs/00_core.ipynb 4
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(message)s')

# %% ../nbs/00_core.ipynb 5
def download_audio(
    vid_id: str, # YouTube video ID
    dest_dir: Path # Output directory
    ):
    "Download audio from YouTube video"
    logging.info(f"Downloading audio for video {vid_id}")
    Path(dest_dir).mkdir(exist_ok=True)
    out_file = Path(dest_dir)/f'{vid_id}.mp3'
    if not out_file.exists():
        subprocess.run(['yt-dlp', '-x', '--audio-format', 'mp3', f'https://www.youtube.com/watch?v={vid_id}', '-o', str(out_file)], check=True)
        logging.info(f"Downloaded audio to {out_file}")
    else:
        logging.info(f"Using existing audio file {out_file}")
    return out_file

# %% ../nbs/00_core.ipynb 7
def detect_silence(audio_file:Path):
    "Detect silence in audio file and return start and end times"
    stream = ffmpeg.input(str(audio_file))
    stream = stream.filter('silencedetect', noise='-30dB', d=0.5)
    stream = stream.output('null', f='null')
    out, err = ffmpeg.run(stream, capture_stderr=True)
    return out, err

# %% ../nbs/00_core.ipynb 9
def parse_silence_ends(stderr_output:bytes):
    "Parse silence ends from ffmpeg stderr output"
    pattern = r'silence_end: ([\d.]+)'
    matches = re.findall(pattern, stderr_output.decode())
    return [float(match) for match in matches]

# %% ../nbs/00_core.ipynb 11
def find_split_points(
    silence_ends:list[float], # silence ends
    total_len:float, # total length of the audio (in seconds)
    chunk_len:float=600 # length of the chunks (in seconds)
    ):
    "Find points to split audio based on silence detection, aiming for chunks of `chunk_len` seconds"
    splits,target = [0],chunk_len
    for t in silence_ends:
        if t >= target:
            splits.append(t)
            target += chunk_len
    splits.append(total_len) # final chunk
    return splits

# %% ../nbs/00_core.ipynb 12
def get_audio_duration(audio_file:"Path|str"):
    "Get duration of audio file in seconds"
    probe = ffmpeg.probe(str(audio_file))
    return float(probe['format']['duration'])

# %% ../nbs/00_core.ipynb 15
def get_mime_type(f): return 'audio/mpeg' if Path(f).suffix.lower() == '.mp3' else 'audio/mp4'

# %% ../nbs/00_core.ipynb 16
def split_audio(
    fname:"Path", # Audio file to split
    splits:"list", # List of timestamps in seconds to split at
    dest_dir:'str|Path'="_audio_chunks"): # Directory to save chunks
    "Split audio file into chunks based on split points"
    Path(dest_dir).mkdir(exist_ok=True)
    chunks = []
    for i, start_time in tqdm(enumerate(splits[:-1]), total=len(splits)-1):
        duration = splits[i+1] - start_time
        chunk_name = f"{fname.stem}_chunk_{i+1:02d}.mp3"
        output_path = Path(dest_dir)/chunk_name
        chunks.append(output_path)
        (ffmpeg
         .input(str(fname), ss=start_time, t=duration)
         .output(str(output_path), acodec='copy')
         .overwrite_output()
         .run(capture_stdout=True, capture_stderr=True))
    return chunks

# %% ../nbs/00_core.ipynb 18
async def transcribe_audio(
    chunks_dir:str|Path,  # Directory containing audio chunks
    dest_file:str|Path, # File to save transcript to
    model:str='gemini-2.0-flash-001', # Gemini model to use
    max_concurrent:int=3,   # Max concurrent transcriptions
    prompt:str="Please transcribe this audio file:" # Custom prompt for transcription
) -> str:
    "Transcribe audio chunks in parallel and combine into single transcript"
    semaphore = asyncio.Semaphore(max_concurrent)
    client = genai.Client(api_key=os.environ['GEMINI_API_KEY'])
    
    async def _transcribe_chunk(chunk_path):
        async with semaphore:
            audio_data = chunk_path.read_bytes()
            audio_part = types.Part.from_bytes(
                mime_type=get_mime_type(chunk_path), 
                data=audio_data
            )
            response = await client.aio.models.generate_content(
                model=model,
                contents=[prompt, audio_part]
            )
            return response.text
    
    chunks = sorted(Path(chunks_dir).glob("*.mp3"))
    tasks = [_transcribe_chunk(chunk) for chunk in chunks]
    transcripts = await asyncio.gather(*tasks)
    
    full_transcript = '\n'.join(transcripts)
    dest_path = Path(dest_file)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(full_transcript)
    return full_transcript
