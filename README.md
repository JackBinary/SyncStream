# SyncStream
[![Pylint](https://github.com/JackBinary/SyncStream/actions/workflows/pylint.yml/badge.svg)](https://github.com/JackBinary/SyncStream/actions/workflows/pylint.yml)
[![Dependencies](https://github.com/JackBinary/SyncStream/actions/workflows/Dependencies.yml/badge.svg)](https://github.com/JackBinary/SyncStream/actions/workflows/Dependencies.yml)
[![Bandit](https://github.com/JackBinary/SyncStream/actions/workflows/bandit.yml/badge.svg)](https://github.com/JackBinary/SyncStream/actions/workflows/bandit.yml)
[![Semgrep](https://github.com/JackBinary/SyncStream/actions/workflows/semgrep.yml/badge.svg)](https://github.com/JackBinary/SyncStream/actions/workflows/semgrep.yml)
[![Docker Image CI](https://github.com/JackBinary/SyncStream/actions/workflows/docker-image.yml/badge.svg)](https://github.com/JackBinary/SyncStream/actions/workflows/docker-image.yml)
[![License](https://img.shields.io/badge/License-Elastic--2.0-blue.svg)](LICENSE)

A lightweight, self-hosted video synchronization service for watching YouTube, Twitch VODs, and raw mp4 files together in real-time.

Originally built as an experiment with local LLM code generation, it turned out to be genuinely useful for watch parties and remote hangouts.

## Features

- Room-based sessions with 6-character codes
- Queue system for multiple videos
- Play/pause/seek sync across all viewers
- YouTube and Twitch embed support
- Chat with nicknames
- Shareable invite links

## Requirements

```
pip install fastapi 'uvicorn[standard]'
```

## Usage

```bash
python syncstream.py
```

Then open http://localhost:8000

## Docker

```bash
docker build -t syncstream .
docker run -p 8000:8000 syncstream
```

Or with docker-compose:

```bash
docker compose up
```

## Security

The app includes rate limiting, input validation, and XSS protection.

## License
This project is licensed under the Elastic License 2.0 - see the [LICENSE](LICENSE) file for details.
