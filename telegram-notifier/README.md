# Run

pip install -r requirements.txt

export $(grep -v '^#' .env | xargs)

uvicorn app:app --host 127.0.0.1 --port 8001



# Send notification

curl -X POST http://localhost:8001/notify \
  -H "Content-Type: application/json" \
  -H "X-API-Key: change-this-to-a-long-random-secret" \
  -d '{
    "service": "Maintenance",
    "target": "default",
    "message": "✅ Database cleanup completed successfully."
  }'
