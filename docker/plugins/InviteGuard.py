import time
from typing import Union
import logging
import json
from synapse.module_api import NOT_SPAM, cached, ModuleApi

class InviteGuard:
    """
    Intercepts the /invite call, and double checks if the user making the request
    is authorized to invite the person.

    WIP: Currently we mock the invitability check by calling "parent-space" and allow invite if room has a parent space. This wil be replaced with real invitability logic later.
    """

    def __init__(self, config: dict, api: ModuleApi):
        self.logger = logging.getLogger(__name__)
        self.logger.info("[VerjiInviteGuard] - VerjiInviteGuard module loaded.")
        self.config = config
        self.api = api

        # cache fields for access token
        self._cached_token = None
        self._token_expiry = 0

        self.api.register_spam_checker_callbacks(
            user_may_invite=self.user_may_invite
        )

        # Register cached functions
        self.api.register_cached_function(self.fakeHasInvitabilityOf)


    async def user_may_invite(
        self, inviter_userid, invitee_userid, room_id
    ) -> Union["synapse.module_api.NOT_SPAM", "synapse.module_api.errors.Codes", bool]:

        self.logger.info(
            "[VerjiInviteGuard] Checking invite: %s inviting %s to room %s",
            inviter_userid,
            invitee_userid,
            room_id,
        )

        login_response = await self.loginClientUser()
        hasInvitebility = await self.fakeHasInvitabilityOf(
            inviter_userid, invitee_userid, room_id, login_response["access_token"]
        )

        self.logger.info("[VerjiInviteGuard] Invitability result: %s", hasInvitebility)

        if hasInvitebility and hasInvitebility.get("roomHadParentLink") is True:
            self.logger.info(
                "[VerjiInviteGuard] - Allowing invite from %s to %s in room %s",
                inviter_userid,
                invitee_userid,
                room_id,
            )
            return NOT_SPAM
        else:
            self.logger.info(
                "[VerjiInviteGuard] - Denying invite from %s to %s in room %s",
                inviter_userid,
                invitee_userid,
                room_id,
            )
            return False

    @cached(uncached_args=["access_token", "room_id"])
    async def fakeHasInvitabilityOf(
        self, inviter_userid, invitee_userid, room_id, access_token
    ):
        """WIP: Placeholder for invitability check, result is cached automatically by Synapse.

        Cache key is (inviter_userid, invitee_userid) only - the relationship between users.
        room_id and access_token are excluded from cache key but still used for API calls.

        Rationale: If an inviter has invitability for an invitee, it's valid across all rooms.
        You don't invite the same person to the same room twice, but you may invite them to
        multiple different rooms.

        In future, when invitability logic is implemented, we may cache different values,
        Most likely: inviter, invitee and spaceId - but for demo purposes we keep it simple, 
        and instead cache if a inviter and invitee - and draw conclusions on this alone.
        """

        # This log only appears when the cached function is actually executed (cache miss)
        self.logger.info(
            "[VerjiInviteGuard] CACHE MISS - Fetching invitability from backend API"
        )

        url = f"https://itopsmx.verji.local/api/v1.1/rooms/{room_id}/parent-space"

        headers = {
            b"Authorization": [f"Bearer {access_token}".encode("utf-8")],
            b"Content-Type": [b"application/json"],
        }

        args = {
            "userId": inviter_userid,
        }

        self.logger.info(
            "[VerjiInviteGuard] Checking invitability: inviter=%s, invitee=%s, room=%s",
            inviter_userid,
            invitee_userid,
            room_id,
        )
        self.logger.debug(
            "[VerjiInviteGuard] Request details - URL: %s, args: %s",
            url,
            args,
        )

        response = await self.api._http_client.get_json(
            url,
            args=args,
            headers=headers,
        )

        self.logger.info("[VerjiInviteGuard] Response: %s", response)
        return response

    async def loginClientUser(self):
        """Fetches and caches OAuth token. Token is reused until 10s before expiry."""
        now = time.time()
        if self._cached_token and now < self._token_expiry:
            self.logger.debug("Reusing cached access token")
            return self._cached_token

        url = "https://id.verji.local/connect/token"
        body = {
            "client_id": self.config["provisioning_user"]["client_id"],
            "client_secret": self.config["provisioning_user"]["client_secret"],
            "grant_type": "client_credentials",
            "audience": "vmx-account",
            "scope": "vmx-account-api",
        }

        response = await self.api._http_client.post_urlencoded_get_json(url, body)

        # cache the token with expiry
        expires_in = response.get("expires_in", 60)  # default 1 min if missing
        self._cached_token = response
        self._token_expiry = now + expires_in - 10  # refresh 10s early

        self.logger.info("Fetched new token, valid for %s seconds", expires_in)
        return response


    # async def hasInvitebilityOf(self, inviter_userid, invitee_userid, room_id, access_token):
    #     url = "https://svc.verji.local/_matrix/client/v3/user_directory/search"
    #     headers = {
    #         "Authorization": f"Bearer {access_token}",
    #         "Content-Type": "application/json"           
    #     }
    #     payload = {
    #         "room_id": room_id,
    #         "search_term": invitee_userid,
    #         "space_id": "",
    #         "tenant_id": ""

    #     }

    #     response = await self.api._http_client.post_json_get_json(
    #         url,
    #         payload,
    #         headers=headers,
    #     )
    #     return response