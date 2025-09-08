from synapse.api.constants import EventTypes
from synapse.module_api import ModuleApi
from synapse.module_api import NOT_SPAM
from synapse.module_api import JsonDict, ModuleApi
from synapse.module_api.errors import ConfigError
from synapse.events import FrozenEvent, FrozenEventV2, FrozenEventV3

import attr
import asyncio
from typing import Any, Dict, Iterable, Set, Union, Optional

from twisted.web.resource import Resource
from twisted.web.server import Request, NOT_DONE_YET
from twisted.internet import defer
from twisted.internet.task import deferLater

import json
import logging

def stringify_keys(d):
    if isinstance(d, dict):
        return {str(k): stringify_keys(v) for k, v in d.items()}
    if isinstance(d, (list, tuple)):
        return type(d)(stringify_keys(v) for v in d)
    return d

class EventEncoder(json.JSONEncoder):
 
    def default(self, e):
 
        if isinstance(e, FrozenEventV2) or isinstance(e, FrozenEventV3):
            return e.get_pdu_json()
        
        return json.JSONEncoder.default(self, e)
    
class EventForwarder:
    """
    Intercept and forward events originating in the matrix platform,
    
    """
    
    def __init__(self, config: dict, api: ModuleApi):
        
        self.redis_connection = None
        self.redis_host = config["redis"]["host"]
        self.redis_port = config["redis"]["port"]        
        self.redis_password = config["redis"]["password"]
        self.redis_stream = config["redis"]["stream"]
        
        self.logger = logging.getLogger(__name__)

        self.config = config
        self.api = api
         
        # Register callbacks which will forward events
        
        self.api.register_spam_checker_callbacks(
            check_event_for_spam=self.check_event_for_spam,
            user_may_create_room=self.user_may_create_room,
            user_may_invite=self.user_may_invite,
            user_may_join_room=self.user_may_join_room,
        )
        
        self.api.register_third_party_rules_callbacks(
            on_new_event=self.on_new_event,
            on_profile_update=self.on_profile_update,
            # on_create_room=self.on_try_create_room      # The event can possibly be denied by other callbacks, hence the "try"
        )

        self.api.register_account_data_callbacks(on_account_data_updated=self.on_account_data_updated)
                
            
    @defer.inlineCallbacks            
    def redis_connect(self):   
        import txredisapi as redis                             
        self.redis_connection = yield redis.Connection(
            self.redis_host, 
            self.redis_port,
            0, 
            True, 
            password=self.redis_password)
         
    @defer.inlineCallbacks                  
    def add_event_to_stream(self, arg): 
        if self.redis_connection == None:
            yield self.redis_connect()
        
        try:
            self.logger.info("Event with type = %s", type(arg))                     
            stringified_dict = stringify_keys(arg)
            payload = json.dumps(stringified_dict, cls=EventEncoder)
        except: 
            self.logger.info("Exception serializing %s", arg)
            raise
        
        #self.logger.info("adding event to stream: %s", payload)
        result = yield self.redis_connection.execute_command("XADD", self.redis_stream, "*", "payload", payload)
        defer.returnValue(result)
        
           
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

        return config
    
                
    async def on_profile_update(self,
        user_id: str,
        new_profile: "synapse.module_api.ProfileInfo",
        by_admin: bool,
        deactivation: bool,
    ) -> None:    
        verji_event = {
            "trigger": "on_profile_update",
            "period": "",
            "user_id": user_id,            
            "new_profile": new_profile,
            "by_admin": by_admin,
            "deactivation": deactivation            
        }
        self.add_event_to_stream(verji_event)           

    async def on_try_create_room(self,
        requester: "synapse.types.Requester",
        request_content: dict,
        is_requester_admin: bool,
        ) -> None:
        verji_event = {
            "trigger": "on_try_create_room",
            "period": "",
            "user_id": requester.user.to_string(),            
            "request_content": request_content,
            "is_requester_admin": is_requester_admin,                   
        }
        self.add_event_to_stream(verji_event)   
        
            
    async def on_new_event(self, event: "synapse.events.EventBase", state_events: "synapse.types.StateMap") -> None:
                        
        verji_event = {
            "trigger": "on_new_event",
            "period": "",
            "event": event,
            "event_id": event.event_id,
            "state_events": state_events
        }
        self.add_event_to_stream(verji_event)   
        return NOT_SPAM


    async def on_account_data_updated(self,
        user_id: str,
        room_id: Optional[str],
        account_data_type: str,
        content: "synapse.module_api.JsonDict",
    ) -> None:                
        verji_event = {
            "trigger": "on_account_data_updated",
            "period": "",
            "user_id": user_id,            
            "room_id": room_id,
            "account_data_type": account_data_type,
            "content": content            
        }
        self.add_event_to_stream(verji_event)   
        
    async def check_event_for_spam(self, event: "synapse.events.EventBase") -> Union["synapse.module_api.NOT_SPAM", "synapse.module_api.errors.Codes", str, bool]            :
                
        verji_event = {
            "trigger": "check_event_for_spam",
            "period": "",
            "event_id": event.event_id,
            "event": event
        }                
        self.add_event_to_stream(verji_event)        
        return NOT_SPAM

    async def user_may_invite(self, inviter_userid, invitee_userid, room_id) -> Union["synapse.module_api.NOT_SPAM", "synapse.module_api.errors.Codes", bool]:
        verji_event = {
            "trigger": "user_may_invite",
            "period": "",
            "inviter": inviter_userid,
            "invitee": invitee_userid,
            "room_id": room_id
        }
        self.add_event_to_stream(verji_event)
        return NOT_SPAM

    async def user_may_join_room(self, user_id: str, room_id: str, is_invited: bool) -> Union["synapse.module_api.NOT_SPAM", "synapse.module_api.errors.Codes", bool]:                        
        verji_event = {
            "trigger": "user_may_join_room",
            "period": "",
            "user_id": user_id,
            "room_id": room_id,
            "is_invited": is_invited
        }
        self.add_event_to_stream(verji_event)
        return NOT_SPAM

    async def user_may_create_room(self, user_id) -> Union["synapse.module_api.NOT_SPAM", "synapse.module_api.errors.Codes", bool]:
        verji_event = {
            "trigger": "user_may_create_room",
            "period": "",
            "user_id":user_id,
        }
        self.add_event_to_stream(verji_event)
        return NOT_SPAM

    async def user_may_create_room_alias(self, user_id, room_alias) -> Union["synapse.module_api.NOT_SPAM", "synapse.module_api.errors.Codes", bool]:
        verji_event = {
            "trigger": "user_may_create_room_alias",
            "period": "",
            "user_id": user_id,
            "room_alias": room_alias
        }
        self.add_event_to_stream(verji_event)
        return NOT_SPAM

    async def user_may_publish_room(self, userid, room_id) -> Union["synapse.module_api.NOT_SPAM", "synapse.module_api.errors.Codes", bool]:
        verji_event = {            
            "trigger": "user_may_publish_room",
            "period": "",
            "userid": userid,
            "room_id": room_id
        }
        self.add_event_to_stream(verji_event)
        return NOT_SPAM

    async def check_username_for_spam(self, user_profile) -> bool:
        verji_event = {
            "trigger": "check_username_for_spam",
            "period": "",
            "user_profile": user_profile,
        }
        self.add_event_to_stream(verji_event)
        return False  # allow all usernames
