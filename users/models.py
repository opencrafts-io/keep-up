from django.db import models
import uuid

from django.utils import timezone


class User(models.Model):
    user_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        primary_key=True,
    )
    name = models.CharField(max_length=512)
    email = models.EmailField(max_length=255, null=True, blank=True)
    phone = models.CharField(max_length=20, null=True, blank=True)
    username = models.CharField(max_length=100, null=True, blank=True)
    avatar_url = models.URLField(max_length=1024, null=True, blank=True)
    vibe_points = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["username"]),
        ]

    def __str__(self):
        """
        Provide a concise display string identifying the user by username and name.

        Returns:
            str: A string formatted as "@{username} - ({name})".
        """
        return f"@{self.username} - ({self.name})"


class OauthToken(models.Model):
    PROVIDER_CHOICES = [
        ("google", "google"),
        ("apple", "apple"),
        ("github", "gitHub"),
    ]

    id = models.UUIDField(default=uuid.uuid4, primary_key=True)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="oauth_tokens",
    )
    provider = models.CharField(
        max_length=60,
        choices=PROVIDER_CHOICES,
        null=False,
    )
    external_user_id = models.CharField(max_length=255, null=False, blank=False)
    access_token = models.TextField(null=True, blank=True)
    access_token_secret = models.TextField(null=True, blank=True)
    refresh_token = models.TextField(null=True, blank=True)
    scopes = models.JSONField(default=list, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [["user", "provider"]]
        indexes = [
            models.Index(fields=["user", "provider"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.provider}"

    def is_expired(self):
        """Check if the access token has expired."""
        if not self.expires_at:
            return False
        return timezone.now() >= self.expires_at

    def has_scope(self, scope: str) -> bool:
        """Check if this token has a specific scope."""
        return scope in self.scopes
