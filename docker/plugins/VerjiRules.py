# -*- coding: utf-8 -*-
# Copyright 2019 The Matrix.org Foundation C.I.C.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import Optional

from synapse.api.constants import EventTypes
from synapse.module_api import ModuleApi
from synapse.module_api.errors import ConfigError

ACCESS_RULES_TYPE = "im.vector.room.access_rules"

class AccessRules:
    DIRECT = "direct"
    RESTRICTED = "restricted"
    UNRESTRICTED = "unrestricted"

class VerjiPowerLevels:
    SystemAdmin = 100
    RosbergAdmin = 95
    CompanyAdmin = 90
    Admin = 80
    Moderator = 50
    Standard = 0

VALID_ACCESS_RULES = (
    AccessRules.DIRECT,
    AccessRules.RESTRICTED,
    AccessRules.UNRESTRICTED,
)

class RoomRules:
    """
    Override power level settings in  Verji. Named power levels are
        * SystemAdmin = 100
        * RosbergAdmin = 95
        * CompanyAdmin = 90
        * Admin = 80
        * Moderator = 50
        * Standard = 0

    VerjiAdmin room:
      * Events: Use Verji standard 
      * Users: 
            * administrator = SystemAdmin/100
            * ?

    Company Admin rooms: 
      * Events: Use Verji standard 
      * Users: Use user power levels from client (companybot)

    DM rooms:
      * Events: Use Verji standard power levels
      * Users: 
            Support is RosbergAdmin/95
            Inviter is Admin/80
            Invitee is Standard/0
    """

    def __init__(self, config: dict, api: ModuleApi):
        self.config = config
        api.register_third_party_rules_callbacks(
            # on_create_room=self.on_create_room,
            check_threepid_can_be_invited=self.check_threepid_can_be_invited,
            check_visibility_can_be_modified=self.check_visibility_can_be_modified,
        )

    @staticmethod
    def parse_config(config: dict) -> dict:
        """Parses and validates the options specified in the homeserver config.
        Args:
            config: The config dict.
        Returns:
            The config dict.
        Raises:
            ConfigError: If there was an issue with the provided module configuration.
        """
        if "rosberg_adminroom_alias_prefix" not in config:
            raise ConfigError("Missing config: rosberg_adminroom_alias_prefix")

        if "company_adminroom_alias_prefix" not in config:
            raise ConfigError("Missing config: company_adminroom_alias_prefix")

        return config

    def _add_configured_power_levels(self, users: dict, powerlevel_override_config_section: str):

        config_users = self.config.get(powerlevel_override_config_section)

        # Add users from config
        if config_users is not None:
            for userid, powerlevel in config_users.items():
                users[userid] = powerlevel


    def _get_default_verji_power_levels(self, user_id: str, is_direct: bool) -> dict:

        inviter_power = VerjiPowerLevels.Admin

        users = {
            user_id: inviter_power
        }

        return {
            "users": users,
            "users_default": 0,
            "events": {
                EventTypes.Name: VerjiPowerLevels.Moderator,
                EventTypes.PowerLevels: VerjiPowerLevels.Admin,
                EventTypes.RoomHistoryVisibility: VerjiPowerLevels.Admin,
                EventTypes.CanonicalAlias: VerjiPowerLevels.Moderator,
                EventTypes.RoomAvatar: VerjiPowerLevels.Moderator,
                EventTypes.Tombstone: VerjiPowerLevels.Admin,
                EventTypes.ServerACL: VerjiPowerLevels.SystemAdmin,
                EventTypes.RoomEncryption: VerjiPowerLevels.Admin,
                EventTypes.ThirdPartyInvite: VerjiPowerLevels.RosbergAdmin,
                EventTypes.Redaction: VerjiPowerLevels.Admin,            # Only Powerlevels above Admin (80) should be allowed to redact
                "m.space.child": -10
            },
            "events_default": VerjiPowerLevels.Standard,
            "state_default": VerjiPowerLevels.Admin,  # Admins should be the only ones to perform other tasks
            "ban": VerjiPowerLevels.Moderator,
            "kick": VerjiPowerLevels.Moderator,
            "redact": VerjiPowerLevels.Admin,           # Only Powerlevels at or above Admin (80) should be allowed to redact
            "invite": VerjiPowerLevels.Moderator,  # All rooms should require mod to invite, even private
        }


    async def on_create_room(
        self,
        requester: "synapse.types.Requester",
        config: dict, # request_content (initial_state)
        is_requester_admin: bool,
    ) -> None:
        """Implements synapse.events.ThirdPartyEventRules.on_create_room.
        Checks if a im.vector.room.access_rules event is being set during room creation.
        If yes, make sure the event is correct. Otherwise, append an event with the
        default rule to the initial state.
        Checks if a m.rooms.power_levels event is being set during room creation.
        If yes, make sure the event is allowed. Otherwise, set power_level_content_override
        in the config dict to our modified version of the default room power levels.
        Args:
            requester: The user who is making the createRoom request.
            config: The createRoom config dict provided by the user.
            is_requester_admin: Whether the requester is a Synapse admin.
        Returns:
            Whether the request is allowed.
        Raises:
            SynapseError: If the createRoom config dict is invalid or its contents blocked.
        """

        is_direct = config.get("is_direct")
        preset = config.get("preset")
        access_rule = None
        room_alias_name = config.get("room_alias_name")
        join_rule = None

        default_power_levels = self._get_default_verji_power_levels(requester.user.to_string(), is_direct)     
        # self.logger.info(f"Default power levels: {config}")
        if room_alias_name is not None and (room_alias_name.startswith(self.config["rosberg_adminroom_alias_prefix"])  or room_alias_name.startswith(self.config["company_adminroom_alias_prefix"])):

            # For the "special" rooms we take users power_levels settings from the request
            # And add any user=>power level mappings specified in the config

            default_power_levels["users"] = config["initial_state"][0]['content']['users']
        
            if room_alias_name is not None and room_alias_name.startswith(self.config["rosberg_adminroom_alias_prefix"]):
                self._add_configured_power_levels(default_power_levels["users"], "rosberg_admin_room_users")

            if room_alias_name is not None and room_alias_name.startswith(self.config["company_adminroom_alias_prefix"]):
                self._add_configured_power_levels(default_power_levels["users"], "company_admin_room_users")

                # For company admin rooms you must be RosbergAdmin to invite
                initial_state = config["initial_state"][0]
            #    if initial_state is not None and len(initial_state['content']) > 1:
             #       config["initial_state"][0]['content']['invite'] = VerjiPowerLevels.RosbergAdmin             
        else:

            # Use the default, and ensure the invitees have the standard PL (0)            
            invitees = config.get("invite")
            if invitees is not None:
                for invitee in invitees:
                    default_power_levels["users"][invitee] = VerjiPowerLevels.Standard

            # add configured default power levels
            self._add_configured_power_levels(default_power_levels["users"], "default_room_users")

        config["power_level_content_override"] = default_power_levels

        # Try to ensure we override settings from room created by bot (which populate 'initial_state')
        initial_state = config["initial_state"][0]
        if initial_state is not None and len(initial_state['content']) > 1:
            config["initial_state"][0]['content']['events'] = default_power_levels['events']
            config["initial_state"][0]['content']['redact'] = default_power_levels['redact']
        
        #Set history_visibility to "shared"
        config["initial_state"][4]['content']['history_visibility'] = "shared"

    async def check_threepid_can_be_invited(
        self,
        medium: str,
        address: str,
        state_events: "synapse.types.StateMap",
    ) -> bool:
        """Implements synapse.events.ThirdPartyEventRules.check_threepid_can_be_invited.
        Check if a threepid can be invited to the room via a 3PID invite given the current
        rules and the threepid's address, by retrieving the HS it's mapped to from the
        configured identity server, and checking if we can invite users from it.
        Args:
            medium: The medium of the threepid.
            address: The address of the threepid.
            state_events: A dict mapping (event type, state key) to state event.
                State events in the room the threepid is being invited to.
        Returns:
            Whether the threepid invite is allowed.
        """

        return False


    async def check_visibility_can_be_modified(
        self,
        room_id: str,
        state_events: "synapse.types.StateMap",
        new_visibility: str
    ) -> bool:
        """Implements
        synapse.events.ThirdPartyEventRules.check_visibility_can_be_modified
        Determines whether a room can be published, or removed from, the public room
        list. A room is published if its visibility is set to "public". Otherwise,
        its visibility is "private". A room with access rule other than "restricted"
        may not be published.
        Args:
            room_id: The ID of the room.
            state_events: A dict mapping (event type, state key) to state event.
                State events in the room.
            new_visibility: The new visibility state. Either "public" or "private".
        Returns:
            Whether the room is allowed to be published to, or removed from, the public
            rooms directory.
        """
        # We need to know the rule to apply when processing the event types below.
        rule = self._get_rule_from_state(state_events)

        # Allow adding a room to the public rooms list only if it is restricted
        if new_visibility == "public":
            return rule == AccessRules.RESTRICTED

        # By default a room is created as "restricted", meaning it is allowed to be
        # published to the public rooms directory.
        return True


    @staticmethod
    def _get_rule_from_state(state_events: "synapse.types.StateMap") -> Optional[str]:
        """Extract the rule to be applied from the given set of state events.
        Args:
            state_events: A dict mapping (event type, state key) to state event.
        Returns:
            The name of the rule (either "direct", "restricted" or "unrestricted") if found,
                else None.
        """
        access_rules = state_events.get((ACCESS_RULES_TYPE, ""))
        if access_rules is None:
            return AccessRules.RESTRICTED

        return access_rules.content.get("rule")        


