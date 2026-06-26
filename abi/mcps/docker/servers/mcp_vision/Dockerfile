FROM python:3.13-alpine
LABEL maintainer="Opteia <abi@opteia.com>"
LABEL org.opteia.mcp="mcp_vision"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ server/
COPY http_adapter.py .

EXPOSE 5110
ENV MCP_PORT=5110

CMD ["python", "http_adapter.py", "server.server", "VisionMCPServer", "--port", "5110"]
