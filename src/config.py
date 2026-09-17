import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    sap_base_url: str
    sap_username: str
    sap_password: str
    sap_client: str
    verify_ssl: bool
    page_size: int
    date_from: str
    date_to: str
    output_dir: str
    log_dir: str


def load_config() -> Config:
    base_url = os.environ.get("SAP_BASE_URL", "").strip().rstrip("/")
    username = os.environ.get("SAP_USERNAME", "").strip()
    password = os.environ.get("SAP_PASSWORD", "").strip()

    missing = [
        name
        for name, value in (
            ("SAP_BASE_URL", base_url),
            ("SAP_USERNAME", username),
            ("SAP_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Missing required .env values: {', '.join(missing)}. "
            "Copy .env.example to .env and fill them in."
        )

    return Config(
        sap_base_url=base_url,
        sap_username=username,
        sap_password=password,
        sap_client=os.environ.get("SAP_CLIENT", "").strip(),
        verify_ssl=os.environ.get("VERIFY_SSL", "true").strip().lower() != "false",
        page_size=int(os.environ.get("ODATA_PAGE_SIZE", "1000")),
        date_from=os.environ.get("SAP_DATE_FROM", "").strip(),
        date_to=os.environ.get("SAP_DATE_TO", "").strip(),
        output_dir=os.environ.get("OUTPUT_DIR", "output").strip(),
        log_dir=os.environ.get("LOG_DIR", "logs").strip(),
    )
