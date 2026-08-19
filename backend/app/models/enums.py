"""Business enumerations.

Stored as VARCHAR rather than native enums so a new value never requires a
schema migration.
"""
from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - display convenience
        return self.value


class UserRole(StrEnum):
    ADMIN = "ADMIN"
    ANALYST = "ANALYST"
    READ_ONLY = "READ_ONLY"


class EntityType(StrEnum):
    PERSON = "PERSON"
    ORGANIZATION = "ORGANIZATION"
    COMPANY = "COMPANY"
    DOMAIN = "DOMAIN"
    PSEUDONYM = "PSEUDONYM"
    OTHER = "OTHER"


class IdentifierType(StrEnum):
    NAME = "NAME"
    FIRST_NAME = "FIRST_NAME"
    LAST_NAME = "LAST_NAME"
    ALIAS = "ALIAS"
    USERNAME = "USERNAME"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    ADDRESS = "ADDRESS"
    CITY = "CITY"
    DEPARTMENT = "DEPARTMENT"
    REGION = "REGION"
    COUNTRY = "COUNTRY"
    DOMAIN = "DOMAIN"
    WEBSITE = "WEBSITE"
    SOCIAL_PROFILE = "SOCIAL_PROFILE"
    COMPANY = "COMPANY"
    ORGANIZATION = "ORGANIZATION"
    PUBLIC_ID = "PUBLIC_ID"
    DATE_OF_BIRTH = "DATE_OF_BIRTH"
    PROFESSION = "PROFESSION"
    NOTE = "NOTE"


#: Contact identifiers. "Former" contacts are flagged by the row's `is_former`
#: column rather than by a separate type.
CONTACT_TYPES = {IdentifierType.EMAIL, IdentifierType.PHONE, IdentifierType.ADDRESS}


class VerificationStatus(StrEnum):
    """Human validation status, shared by identifiers and profiles."""

    UNKNOWN = "UNKNOWN"
    HYPOTHESIS = "HYPOTHESIS"
    PROBABLE = "PROBABLE"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class FindingStatus(StrEnum):
    NEW = "NEW"
    CONFIRMED = "CONFIRMED"
    PROBABLE = "PROBABLE"
    UNVERIFIED = "UNVERIFIED"
    REJECTED = "REJECTED"
    OUTDATED = "OUTDATED"
    CONTRADICTED = "CONTRADICTED"


class FindingType(StrEnum):
    SOCIAL_PROFILE = "social_profile"
    ACCOUNT_EXISTS = "account_exists"
    OBFUSCATED_CONTACT = "obfuscated_contact"
    PROFILE_METADATA = "profile_metadata"
    PHONE_INFO = "phone_info"
    WEB_RESULT = "web_result"
    SEARCH_QUERY = "search_query"
    IDENTITY_MATCH = "identity_match"
    CONTRADICTION = "contradiction"
    OTHER = "other"


class PlatformCategory(StrEnum):
    SOCIAL_NETWORK = "social_network"
    FORUM = "forum"
    DEVELOPER = "developer"
    GAMING = "gaming"
    PROFESSIONAL = "professional"
    VIDEO = "video"
    MUSIC = "music"
    BLOG = "blog"
    MARKETPLACE = "marketplace"
    OTHER = "other"


class RelationshipType(StrEnum):
    HAS_EMAIL = "HAS_EMAIL"
    HAS_PHONE = "HAS_PHONE"
    USES_USERNAME = "USES_USERNAME"
    EXISTS_ON = "EXISTS_ON"
    HAS_PROFILE = "HAS_PROFILE"
    WORKS_FOR = "WORKS_FOR"
    ASSOCIATED_WITH = "ASSOCIATED_WITH"
    FOUND_BY = "FOUND_BY"
    SUPPORTED_BY = "SUPPORTED_BY"
    POSSIBLE_SAME_AS = "POSSIBLE_SAME_AS"
    RELATED_TO = "RELATED_TO"
    LOCATED_IN = "LOCATED_IN"
    VARIANT_OF = "VARIANT_OF"


class RunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


class SourceKind(StrEnum):
    """Source category, which drives the default reliability rating."""

    OFFICIAL_API = "official_api"
    OFFICIAL_WEBSITE = "official_website"
    VERIFIED_SOURCE = "verified_source"
    ESTABLISHED_DATABASE = "established_database"
    SEARCH_ENGINE = "search_engine"
    UNVERIFIED_WEBSITE = "unverified_website"
    USER_HYPOTHESIS = "user_hypothesis"
    MANUAL_ENTRY = "manual_entry"
    TOOL_OUTPUT = "tool_output"
