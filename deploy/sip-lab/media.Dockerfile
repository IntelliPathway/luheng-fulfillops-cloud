FROM python:3.12-slim
RUN pip install --no-cache-dir httpx==0.28.1 websockets==17.2 \
    && groupadd --gid 10001 media_lab && useradd --uid 10001 --gid 10001 --no-create-home media_lab \
    && mkdir -p /var/lib/media-lab && chown media_lab:media_lab /var/lib/media-lab && chmod 700 /var/lib/media-lab
WORKDIR /opt/media-lab
COPY backend/app/__init__.py backend/app/sip_lab.py backend/app/sip_lab_media.py backend/app/sip_lab_bridge.py ./app/
COPY backend/app/sip_lab_voice.py backend/app/sip_lab_voice_session.py ./app/
CMD ["python", "-m", "app.sip_lab_bridge"]
