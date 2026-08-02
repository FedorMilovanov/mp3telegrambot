"""Processing pipelines — video, shorts, clips, montage, playlist."""
from services.shorts_caption_contract_runtime import (
    install_short_caption_contract_runtime,
)

# Install before pipelines.shorts copies build_short_caption from the service
# module. The guard is narrow, idempotent and affects Shorts title casing only.
install_short_caption_contract_runtime()
