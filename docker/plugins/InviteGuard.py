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
        self.logger.info("InviteGuard module loaded")
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
        # self.api.register_cached_function(self.fakeHasInvitabilityOf)


    async def user_may_invite(
        self, inviter_userid, invitee_userid, room_id
    ) -> Union["synapse.module_api.NOT_SPAM", "synapse.module_api.errors.Codes", bool]:

        login_response = await self.loginClientUser()
        hasInvitebility = await self.fakeHasInvitabilityOf(
            inviter_userid, invitee_userid, room_id, login_response["access_token"]
        )

        self.logger.info("InviteGuard response - %s", hasInvitebility)

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

    @cached()
    async def fakeHasInvitabilityOf(
        self, inviter_userid, invitee_userid, room_id, access_token
    ):
        """Checks invitability, result is cached automatically by Synapse."""

        url = f"https://itopsmx.verji.local/api/v1.1/rooms/{room_id}/parent-space"

        headers = {
            b"Authorization": [f"Bearer {access_token}".encode("utf-8")],
            b"Content-Type": [b"application/json"],
        }

        args = {
            "userId": inviter_userid,
        }

        self.logger.info(
            "[VerjiInviteGuard] Calling %s with headers %s and args %s",
            url,
            headers,
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