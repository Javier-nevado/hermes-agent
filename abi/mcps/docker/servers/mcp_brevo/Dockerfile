FROM python:3.13-alpine
LABEL maintainer="Opteia <abi@opteia.com>"
LABEL org.opteia.mcp="mcp_brevo"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ server/
COPY http_adapter.py .

VOLUME /credentials

EXPOSE 5109
ENV MCP_PORT=5109

CMD ["python", "http_adapter.py", "server.server", "BrevoMCPServer", "--port", "5109"]
