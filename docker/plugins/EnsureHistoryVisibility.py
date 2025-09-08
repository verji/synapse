from synapse.module_api import ModuleApi

import logging
    
class EnsureHistoryVisibility:
    """
    Intercept all on_create_room events and tries to override the history_visibility to "shared".
    
    """
    
    def __init__(self, config: dict, api: ModuleApi):
        
        self.logger = logging.getLogger(__name__)
        self.logger.info("EnsureHistoryVisibility module loaded")
        self.config = config
        self.api = api
        api.register_third_party_rules_callbacks(
            on_create_room=self.on_create_room,
        )
    
    async def on_create_room(
        self,
        requester: "synapse.types.Requester",
        request_content: dict,
        is_requester_admin: bool,
    ) -> None:
        """
        Implements synapse.events.ThirdPartyEventRules.on_create_room.
        Ensures history_visibility is set to "shared" when a room is created.

        Args:
            requester: The user creating the room.
            request_content: The content of the room creation request, including initial_state.
            is_requester_admin: Whether the requester has admin privileges.
        Returns:
            None
        """

        try:
            self.logger.info(
                f"on_create_room called for user {requester.user.to_string()}"
            )

            # Extract initial_state safely
            initial_state = request_content.get("initial_state", [])
            if not isinstance(initial_state, list):
                self.logger.error("initial_state is not a list, aborting EnsureHistoryVisibility...")
                return

            # Find existing history_visibility event, this is safe because we default to False instead of StopInteration
            history_event = next(
                (ev for ev in initial_state if ev.get("type") == "m.room.history_visibility"),
                False,  # Return False if not found instead of raising StopIteration (explicitly)
            )

            if history_event:
                content = history_event.get("content")
                if isinstance(content, dict) and "history_visibility" in content:
                    content["history_visibility"] = "shared"
                else:
                    self.logger.error(f"Cannot set history_visibility: content not usable as dict: {content}")
                    self.logger.error("Aborting EnsureHistoryVisibility.")

                    return
            else:
                self.logger.warning(f"No history_visibility event found in initial_state. {initial_state}")
                # If we want to enforce adding history_visibility if not present, currently we are satisfied with logging a warning. Maybe we want to add config to the module to toggle this behavior?
                # Add a new event if not found
                # initial_state.append(
                #     {
                #         "type": "m.room.history_visibility",
                #         "sender": requester.user.to_string(),
                #         "state_key": "",
                #         "content": {"history_visibility": "shared"},
                #     }
                # )
                # self.logger.debug("Added new history_visibility event with 'shared'.")
                
        except Exception as e:
            self.logger.exception(f"Failed to enforce history_visibility: {e}")

