"""
Session Controller - Manages room context, session canvas, and mix restore logic.

This module handles:
- Room context persistence (saving/loading room state)
- Session canvas management (refresh, state sync)
- Mix restore operations (restoring saved mixer states)
"""

import logging
from typing import Dict, Any, Optional, Callable, List
from core.creative_modes import get_mode_by_key_or_default, get_mode_by_label_or_default
from core.session_templates import get_templates_for_mode, SESSION_TEMPLATES

logger = logging.getLogger(__name__)


class SessionController:
    """Manages session state, room context, and canvas operations"""
    
    def __init__(self, app):
        self.app = app
        self.repository = app.repository
        self.room_key = app.room_key
        
    @property
    def mode_key(self) -> str:
        return getattr(self.app, 'mode_key', 'music_jam')
    
    @mode_key.setter
    def mode_key(self, value: str) -> None:
        self.app.mode_key = value
        
    @property
    def template_name(self) -> str:
        return getattr(self.app, 'template_name', '')
    
    @template_name.setter
    def template_name(self, value: str) -> None:
        self.app.template_name = value
        
    @property
    def session_goal_text(self) -> str:
        return getattr(self.app, 'session_goal_text', '')
    
    @session_goal_text.setter
    def session_goal_text(self, value: str) -> None:
        self.app.session_goal_text = value
        
    @property
    def template_var(self):
        return getattr(self.app, 'template_var', None)
        
    @property
    def session_goal_var(self):
        return getattr(self.app, 'session_goal_var', None)
        
    @property
    def session_canvas(self):
        return getattr(self.app, 'session_canvas', None)
        
    @property
    def mixer_service(self):
        return getattr(self.app, 'mixer_service', None)
    
    def get_room_context(self) -> Dict[str, Any]:
        """Load room context from repository"""
        return self.repository.get_room_context(self.room_key)
    
    def save_room_context(self) -> None:
        """Save current room state to repository"""
        template_value = self.template_var.get() if self.template_var else self.template_name
        session_goal_value = self.session_goal_var.get() if self.session_goal_var else self.session_goal_text
        
        template_name = str(template_value).strip() if template_value is not None else ""
        session_goal = str(session_goal_value).strip() if session_goal_value is not None else ""
        
        active_mode = get_mode_by_key_or_default(self.mode_key)
        
        if not template_name:
            template_name = active_mode.default_template
        if not session_goal:
            session_goal = active_mode.default_goal
            
        context = {
            "mode_key": self.mode_key,
            "template_name": template_name,
            "session_goal": session_goal,
            "review_state": getattr(self.app, 'review_state', 'draft'),
        }
        
        self.repository.save_room_context(self.room_key, context)
        logger.debug("Room context saved: %s", context)
        
    def on_mode_selected(self, mode_label: str) -> None:
        """Handle mode selection change"""
        selected = get_mode_by_label_or_default(mode_label)
        old_mode = get_mode_by_key_or_default(self.mode_key)
        
        self.mode_key = selected.key
        
        if not self.template_var.get().strip():
            self.template_var.set(selected.default_template)
        if not self.session_goal_var.get().strip():
            self.session_goal_var.set(selected.default_goal)
            
        self.save_room_context()
        
        if self.session_canvas and hasattr(self.session_canvas, 'refresh'):
            self.session_canvas.refresh()
            
        # Apply mode layout if mode controller exists
        if hasattr(self.app, 'mode_controller'):
            self.app.mode_controller.apply_layout(selected.key)
            
        logger.info("Mode changed from %s to %s", old_mode.label, selected.label)
        
    def on_quick_template_selected(self, template_label: str) -> None:
        """Handle quick template selection"""
        if template_label == "— Custom —":
            return
            
        templates = get_templates_for_mode(self.mode_key)
        selected = None
        for t in templates:
            if t.label == template_label:
                selected = t
                break
                
        if selected:
            self.template_var.set(selected.template_name)
            self.session_goal_var.set(selected.session_goal)
            self.save_room_context()
            
            if self.session_canvas and hasattr(self.session_canvas, 'refresh'):
                self.session_canvas.refresh()
                
            logger.info("Template selected: %s", selected.label)
            
    def on_review_state_change(self, new_state: str) -> None:
        """Handle review state changes"""
        valid_states = {"draft", "review", "final"}
        if new_state not in valid_states:
            logger.warning("Invalid review state: %s", new_state)
            return
            
        self.app.review_state = new_state
        self.save_room_context()
        logger.info("Review state changed to: %s", new_state)
        
    def refresh_canvas(self) -> None:
        """Refresh the session canvas display"""
        if self.session_canvas and hasattr(self.session_canvas, 'refresh'):
            self.session_canvas.refresh()
            
    def attempt_mix_restore(self) -> None:
        """Attempt to restore a pending mix state"""
        if self.mixer_service and hasattr(self.mixer_service, '_attempt_pending_mix_restore'):
            self.mixer_service._attempt_pending_mix_restore()
            
    def get_participants(self) -> List[Dict[str, Any]]:
        """Get current participants from Jamulus controller"""
        if hasattr(self.app, 'jamulus_controller'):
            return self.app._bridge_participants()
        return []
        
    def get_templates_for_current_mode(self) -> List[str]:
        """Get available templates for current mode"""
        return [t.label for t in get_templates_for_mode(self.mode_key)]