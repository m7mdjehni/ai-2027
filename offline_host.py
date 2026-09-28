"""Local project/validation helper; no Hermes or Codex runtime is required."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name('.env'))
os.environ['AI_PROVIDER'] = 'ollama'
os.environ['PROJECT_AI_ENGINE'] = 'ollama'
os.environ['CHAT_AI_ENGINE'] = 'ollama'

from hermes_service import app

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8766)
