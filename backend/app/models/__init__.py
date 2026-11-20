"""SQLAlchemy models (BUILD_SPEC section 5). Importing this package registers every table on ``Base.metadata``."""

from app.models.ai import LlmKey, Secret, UsageEvent
from app.models.audit import AuditLog
from app.models.auth import RefreshToken, Session, UserToken
from app.models.billing import BillingEvent
from app.models.chat import Conversation, Message, SavedQuestion
from app.models.connection import (
    Connection,
    ConnectionGrant,
    Dataset,
    FileVersion,
    Snapshot,
)
from app.models.consent import Consent
from app.models.dictionary import DictionaryVersion
from app.models.goals import DashboardTile, Goal
from app.models.invitation import Invitation
from app.models.org import Membership, Organization
from app.models.reports import BriefingRun, BriefingSetting, Report, Share, Task
from app.models.tally import TallyDevice, TallyRecord, TallySync
from app.models.two_factor import KnownDevice, LoginChallenge, TwoFactor, TwoFactorCode
from app.models.user import User

__all__ = [
    "AuditLog",
    "BillingEvent",
    "BriefingRun",
    "BriefingSetting",
    "Connection",
    "ConnectionGrant",
    "Consent",
    "Conversation",
    "DashboardTile",
    "Dataset",
    "DictionaryVersion",
    "FileVersion",
    "Goal",
    "Invitation",
    "KnownDevice",
    "LlmKey",
    "LoginChallenge",
    "Membership",
    "Message",
    "Organization",
    "RefreshToken",
    "Report",
    "SavedQuestion",
    "Secret",
    "Session",
    "Share",
    "Snapshot",
    "TallyDevice",
    "TallyRecord",
    "TallySync",
    "Task",
    "TwoFactor",
    "TwoFactorCode",
    "UsageEvent",
    "User",
    "UserToken",
]
