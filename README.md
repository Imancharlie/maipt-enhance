# MiPT AI Enhancement Service

A FastAPI-based microservice for AI-powered weekly report enhancement using Anthropic Claude.

## Setup

1. Create virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure environment variables:
```bash
cp .env.example .env
# Edit .env with your actual API keys
```

4. Run the service:
```bash
# Development
./run.sh

# Or manually:
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Testing

Run the test script:
```bash
python test_vps_service.py
```

## API Endpoints

- `POST /api/weekly/enhance` - Enhance weekly report with AI
- `GET /api/weekly/health` - Health check
- `GET /` - Service info

## Architecture

- **Framework**: FastAPI (lightweight, async)
- **AI Provider**: Anthropic Claude 3 Haiku
- **Authentication**: Shared API key (X-API-Key header)
- **Session Management**: In-memory with auto-cleanup (2-hour TTL)
- **Data Privacy**: Only anonymized metadata stored (hashed user IDs)

## Deployment

### VPS Setup

1. SSH into VPS
2. Install Python 3.10+
3. Clone this repository
4. Follow setup steps above
5. Configure firewall: `sudo ufw allow 8000/tcp`

### Production with systemd

Create `/etc/systemd/system/mipt-ai.service`:
```ini
[Unit]
Description=MiPT AI Enhancement Service
After=network.target

[Service]
User=mipt
WorkingDirectory=/home/mipt/mipt-ai-service
Environment="PATH=/home/mipt/mipt-ai-service/venv/bin"
ExecStart=/home/mipt/mipt-ai-service/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable mipt-ai
sudo systemctl start mipt-ai
sudo systemctl status mipt-ai
```

## Security Notes

- Never commit `.env` file to version control
- Use strong API keys
- Only allow CORS from trusted origins (PythonAnywhere backend)
- Sessions auto-expire after 2 hours
- User IDs are hashed before logging (no personal data stored)
