FROM python:3.13-alpine
LABEL maintainer="Opteia <abi@opteia.com>"
LABEL org.opteia.mcp="mcp_proxmox"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ server/
COPY http_adapter.py .

EXPOSE 5113
ENV MCP_PORT=5113

CMD ["python", "http_adapter.py", "server.server", "ProxmoxMCPServer", "--port", "5113"]
