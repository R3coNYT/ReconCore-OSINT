"""SQLAlchemy models. Importing this module registers every table."""
from app.db.base import Base
from app.models.enums import (
    EntityType,
    FindingStatus,
    FindingType,
    IdentifierType,
    PlatformCategory,
    RelationshipType,
    RiskLevel,
    RunStatus,
    SourceKind,
    UserRole,
    VerificationStatus,
)
from app.models.evidence import (
    Contradiction,
    Finding,
    Relationship,
    Source,
    TimelineEvent,
)
from app.models.identity import Identifier, Platform, SocialProfile, Username
from app.models.investigation import (
    Investigation,
    Note,
    Organization,
    Person,
    Tag,
    person_tags,
)
from app.models.ops import (
    PluginRegistryEntry,
    PluginRun,
    PluginSecret,
    Search,
    SearchResult,
)
from app.models.user import AuditLog, RefreshToken, User

__all__ = [
    "Base",
    "AuditLog",
    "Contradiction",
    "EntityType",
    "Finding",
    "FindingStatus",
    "FindingType",
    "Identifier",
    "IdentifierType",
    "Investigation",
    "Note",
    "Organization",
    "Person",
    "Platform",
    "PlatformCategory",
    "PluginRegistryEntry",
    "PluginRun",
    "PluginSecret",
    "RefreshToken",
    "Relationship",
    "RelationshipType",
    "RiskLevel",
    "RunStatus",
    "Search",
    "SearchResult",
    "SocialProfile",
    "Source",
    "SourceKind",
    "Tag",
    "TimelineEvent",
    "User",
    "UserRole",
    "Username",
    "VerificationStatus",
    "person_tags",
]
