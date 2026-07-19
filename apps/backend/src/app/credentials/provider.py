import base64
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken


class CredentialError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EncryptedEnvelope:
    ciphertext: str
    key_version: str


class CredentialCipher(ABC):
    @abstractmethod
    def encrypt(self, plaintext: str) -> EncryptedEnvelope: ...

    @abstractmethod
    def decrypt(self, envelope: EncryptedEnvelope) -> str: ...


class LocalFernetCipher(CredentialCipher):
    """Development cipher. Production should implement this interface with AWS KMS."""

    def __init__(self, key: str, key_version: str = "local-v1") -> None:
        if not key:
            raise CredentialError("BACKEND_ENCRYPTION_KEY must be configured")
        try:
            raw = key.encode()
            if len(raw) != 44:
                raw = base64.urlsafe_b64encode(key.encode().ljust(32, b"\0")[:32])
            self._fernet = Fernet(raw)
        except ValueError as exc:
            raise CredentialError("Invalid encryption key") from exc
        self.key_version = key_version

    @classmethod
    def generate_key(cls) -> str:
        return Fernet.generate_key().decode()

    def encrypt(self, plaintext: str) -> EncryptedEnvelope:
        if not plaintext:
            raise CredentialError("Credential value cannot be empty")
        return EncryptedEnvelope(
            self._fernet.encrypt(plaintext.encode()).decode(), self.key_version
        )

    def decrypt(self, envelope: EncryptedEnvelope) -> str:
        if envelope.key_version != self.key_version:
            raise CredentialError("Unsupported key version")
        try:
            return self._fernet.decrypt(envelope.ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise CredentialError("Credential authentication failed") from exc


class AwsKmsCipher(CredentialCipher):
    """Production envelope provider backed by an AWS KMS customer-managed key."""

    def __init__(self, key_id: str, region_name: str, key_version: str = "kms-v1") -> None:
        if not key_id:
            raise CredentialError("AWS_KMS_KEY_ID must be configured")
        import boto3  # type: ignore[import-untyped]

        self._client = boto3.client("kms", region_name=region_name)
        self.key_id = key_id
        self.key_version = key_version

    def encrypt(self, plaintext: str) -> EncryptedEnvelope:
        if not plaintext:
            raise CredentialError("Credential value cannot be empty")
        response = self._client.encrypt(
            KeyId=self.key_id,
            Plaintext=plaintext.encode(),
            EncryptionContext={"application": "multi-tenant-agent-saas"},
        )
        ciphertext = base64.b64encode(response["CiphertextBlob"]).decode()
        return EncryptedEnvelope(ciphertext, self.key_version)

    def decrypt(self, envelope: EncryptedEnvelope) -> str:
        if envelope.key_version != self.key_version:
            raise CredentialError("Unsupported key version")
        try:
            response = self._client.decrypt(
                CiphertextBlob=base64.b64decode(envelope.ciphertext),
                EncryptionContext={"application": "multi-tenant-agent-saas"},
            )
            return response["Plaintext"].decode()
        except Exception as exc:
            raise CredentialError("KMS credential decryption failed") from exc


def ephemeral_cipher() -> LocalFernetCipher:
    return LocalFernetCipher(base64.urlsafe_b64encode(os.urandom(32)).decode())
