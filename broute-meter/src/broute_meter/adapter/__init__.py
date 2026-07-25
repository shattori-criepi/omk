"""交換可能なBルートアダプター境界とRS-WSUHA-P実装。"""

from broute_meter.adapter.base import (
    AdapterCommunicationError,
    AdapterConfigurationResult,
    AdapterCredentialError,
    AdapterError,
    AdapterPanaJoinError,
    AdapterResponseTimeoutError,
    AdapterScanError,
    AdapterSettingError,
    AdapterSetupResult,
    AdapterTimeoutError,
    AdapterVerificationError,
    BaseAdapter,
    InvalidAdapterSettingValueError,
    UnsupportedAdapterSettingError,
)
from broute_meter.adapter.mock import MockAdapter
from broute_meter.adapter.rs_wsuha_p import (
    RS_WSUHA_P_B_ROUTE_COMMANDS,
    RS_WSUHA_P_SETTING_COMMANDS,
    AdapterSettingCommand,
    RSWSUHAPAdapter,
    RsWsuhaPAdapter,
)

__all__ = [
    "AdapterCommunicationError",
    "AdapterConfigurationResult",
    "AdapterCredentialError",
    "AdapterError",
    "AdapterPanaJoinError",
    "AdapterResponseTimeoutError",
    "AdapterScanError",
    "AdapterSettingCommand",
    "AdapterSettingError",
    "AdapterSetupResult",
    "AdapterTimeoutError",
    "AdapterVerificationError",
    "BaseAdapter",
    "InvalidAdapterSettingValueError",
    "MockAdapter",
    "RSWSUHAPAdapter",
    "RS_WSUHA_P_B_ROUTE_COMMANDS",
    "RS_WSUHA_P_SETTING_COMMANDS",
    "RsWsuhaPAdapter",
    "UnsupportedAdapterSettingError",
]
