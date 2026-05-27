FROM python:3.13-alpine
LABEL maintainer="Opteia <abi@opteia.com>"
LABEL org.opteia.mcp="mcp_twenty"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ server/
COPY http_adapter.py .

EXPOSE 5111
ENV MCP_PORT=5111

CMD ["python", "http_adapter.py", "server.server", "TwentyMCPServer", "--port", "5111"]
