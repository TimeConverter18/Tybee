import uvicorn

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "panel.main:app",
        host="0.0.0.0",
        port=settings.web_port,
        log_level="info",
        forwarded_allow_ips=settings.web_forwarded_allow_ips,
    )


if __name__ == "__main__":
    main()
