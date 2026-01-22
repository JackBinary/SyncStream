FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi 'uvicorn[standard]'
COPY syncstream.py .
EXPOSE 8000
CMD ["uvicorn", "syncstream:app", "--host", "0.0.0.0", "--port", "8000"]