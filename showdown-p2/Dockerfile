FROM python:3.12-slim

WORKDIR /app
COPY app.py bot.py rule_model.py ./

ENV PORT=5000
EXPOSE 5000
CMD ["python", "app.py"]
