import base64
import asyncio
import os
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
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
    """Local Fernet cipher with a small keyring for rotation.

    Encrypt always uses the current key_version. Decrypt selects the Fernet
    instance matching envelope.key_version (current or previous).
    """

    def __init__(
        self,
        key: str,
        key_version: str = "local-v1",
        *,
        previous_keys: dict[str, str] | None = None,
    ) -> None:
        if not key:
            raise CredentialError("BACKEND_ENCRYPTION_KEY must be configured")
        self.key_version = key_version
        self._fernets: dict[str, Fernet] = {
            key_version: self._fernet_from_key(key),
        }
        for version, prior in (previous_keys or {}).items():
            if version and prior and version not in self._fernets:
                self._fernets[version] = self._fernet_from_key(prior)

    @staticmethod
    def _fernet_from_key(key: str) -> Fernet:
        try:
            raw = key.encode()
            if len(raw) != 44:
                raw = base64.urlsafe_b64encode(key.encode().ljust(32, b"\0")[:32])
            return Fernet(raw)
        except ValueError as exc:
            raise CredentialError("Invalid encryption key") from exc

    @classmethod
    def generate_key(cls) -> str:
        return Fernet.generate_key().decode()

    def encrypt(self, plaintext: str) -> EncryptedEnvelope:
        if not plaintext:
            raise CredentialError("Credential value cannot be empty")
        return EncryptedEnvelope(
            self._fernets[self.key_version].encrypt(plaintext.encode()).decode(),
            self.key_version,
        )

    def decrypt(self, envelope: EncryptedEnvelope) -> str:
        fernet = self._fernets.get(envelope.key_version)
        if fernet is None:
            raise CredentialError(
                f"Unsupported key version {envelope.key_version!r}; "
                "add it to ENCRYPTION_PREVIOUS_KEYS to rotate safely"
            )
        try:
            return fernet.decrypt(envelope.ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise CredentialError("Credential authentication failed") from exc


class AwsKmsCipher(CredentialCipher):
    """Production envelope provider backed by an AWS KMS customer-managed key."""

    _executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="kms")

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
        # KMS ciphertext is self-describing; version tag is informational.
        try:
            response = self._client.decrypt(
                CiphertextBlob=base64.b64decode(envelope.ciphertext),
                EncryptionContext={"application": "multi-tenant-agent-saas"},
            )
            return response["Plaintext"].decode()
        except Exception as exc:
            raise CredentialError("KMS credential decryption failed") from exc

    async def aencrypt(self, plaintext: str) -> EncryptedEnvelope:
        return await asyncio.to_thread(self.encrypt, plaintext)

    async def adecrypt(self, envelope: EncryptedEnvelope) -> str:
        return await asyncio.to_thread(self.decrypt, envelope)


def ephemeral_cipher() -> LocalFernetCipher:
    return LocalFernetCipher(base64.urlsafe_b64encode(os.urandom(32)).decode())
