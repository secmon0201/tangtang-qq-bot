"""Single-story director for the 今日缘分 social game.

The game used to have separate random sources for draw cards, interactions,
and conclusions.  This module is deliberately pure: callers give it the
current daily script, relationship state, and resolved effects, then persist
the returned plan.  That keeps every visible card in one scene and lets a
later action explicitly continue an earlier unresolved moment.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from bot.services.today_wife_content import StoryBeat, StoryChoice, StoryTransition, THEME_PACKS, pack_for, script_for


INTENT_LABELS: dict[str, str] = {
    "auto": "随缘行动",
    "靠近": "主动靠近",
    "倾听": "认真倾听",
    "回应": "回应缘分",
    "修复": "修复关系",
    "助攻": "替人助攻",
}

PHASE_LABELS: dict[str, str] = {
    "first_spark": "刚刚相遇",
    "warming": "正在升温",
    "awkward": "有点尴尬",
    "fractured": "需要修复",
    "repairing": "正在修复",
    "answered": "等到回应",
    "separated": "已经告别",
}


_RELATIONSHIP_LABELS: dict[str, tuple[str, ...]] = {
    "night_convenience": ("夜班共守者", "热可可留位人", "便利店门口同行者"),
    "last_subway": ("末班同行者", "换乘口同路人", "车门边的等候者"),
    "night_market": ("收摊同行者", "夜市最后一位客人", "摊前留位人"),
    "reverse_station_clock": ("倒计时同路人", "折返站同行者", "晚点一分钟搭子"),
    "talking_vending_machine": ("秘密投递对象", "自动贩卖机同谋", "答案保管人"),
    "drift_mailbox": ("漂流地址同行者", "回信等候人", "未署名的收件人"),
    "memory_pawnshop": ("记忆保管人", "典当行同路者", "旧事见证人"),
    "moonlight_bazaar": ("月光摊前同行者", "许愿币共有人", "夜色里的同伴"),
    "sky_rail": ("云层列车同行者", "下一站共乘人", "天轨换乘搭子"),
}


class StoryDirector:
    """Build compact, causally connected narrative plans from resolved state."""

    @staticmethod
    def context(theme_id: str, script_id: str) -> dict[str, Any]:
        pack = pack_for(str(theme_id))
        script = script_for(str(script_id), pack.id)
        return {
            "scene_id": script.id,
            "theme_id": pack.id,
            "theme_title": pack.title,
            "style": pack.style,
            "script_id": script.id,
            "title": script.title,
            "prop": script.prop,
            "opening": script.opening,
            "acts": tuple(script.acts),
            "mechanisms": tuple(pack.mechanisms),
            "setup_fact": script.setup_fact,
            "central_question": script.central_question,
            "facts": tuple(script.facts),
            "choices": dict(script.choices),
            "outcomes": dict(script.outcomes),
            "endings": tuple(script.endings),
            "start_beat_id": str(script.start_beat_id),
            "beats": dict(script.beats),
            "ending_by_id": dict(script.ending_by_id),
        }

    @classmethod
    def context_from_day_state(cls, day_state: Mapping[str, Any]) -> dict[str, Any]:
        return cls.context(str(day_state["theme_id"]), str(day_state["script_id"]))

    @classmethod
    def context_for_story_id(cls, story_id: str) -> dict[str, Any] | None:
        """Return a unified scene only for new script IDs, not legacy episode IDs."""

        for pack in THEME_PACKS:
            for script in pack.scripts:
                if script.id == str(story_id):
                    return cls.context(pack.id, script.id)
        return None

    @classmethod
    def relationship_label(cls, context: Mapping[str, Any], record: Mapping[str, Any]) -> str:
        title = str(context.get("title") or "今日故事")
        prop = str(context.get("prop") or "这条线索")
        # Relationship labels are scene facts too.  A ticket script must never
        # inherit a heat-cocoa title simply because both live in one theme.
        labels = (f"{title}同行者", f"{title}的{prop}回应者", f"{title}的另一位主角")
        return cls._pick(labels, cls._record_seed(record, "relationship"))

    @classmethod
    def initial_arc(
        cls,
        context: Mapping[str, Any],
        record: Mapping[str, Any],
        relationship_label: str,
    ) -> dict[str, Any]:
        prop = str(context["prop"])
        actor = cls._name(record.get("actor_nickname"))
        target = cls._name(record.get("target_nickname"))
        hook_id = f"draw:{int(record.get('actor_id') or 0)}:{int(record.get('draw_index') or 1)}"
        beat_id = str(context.get("start_beat_id") or "opening")
        beat = cls._beat_for(context, beat_id)
        hook_summary = cls._format(
            str(beat.hook if beat is not None else f"{prop}还在等一个不仓促的回应"),
            actor=actor,
            target=target,
            left=actor,
            right=target,
            prop=prop,
        )
        return {
            "version": 3,
            "scene_id": str(context["scene_id"]),
            "phase": "first_spark",
            "question": str(context.get("central_question") or f"{actor}和{target}会怎样回应{prop}留下的线索？"),
            "facts": [str(context.get("setup_fact") or f"{prop}还留在故事开始的位置。")],
            "beat_id": beat_id,
            "hook": {
                "id": hook_id,
                "kind": "first_move",
                "summary": hook_summary,
                "status": "open",
                "source_event_id": None,
            },
            "last_summary": "这段关系刚刚开始。",
            "previous_event_id": None,
            "recent_beat_ids": [hook_id],
            "relationship_label": relationship_label,
            "relationship_origin": str(record.get("draw_source") or "random"),
            "branch": {
                "beat_id": beat_id,
                "last_choice_id": "",
                "last_outcome_id": "",
                "last_ending_id": "",
                "history": [],
            },
        }

    @classmethod
    def compose_draw_reveal(
        cls,
        context: Mapping[str, Any],
        record: Mapping[str, Any],
        relation: Mapping[str, Any],
    ) -> dict[str, Any]:
        actor = cls._name(record.get("actor_nickname"))
        target = cls._name(record.get("target_nickname"))
        prop = str(context["prop"])
        branch = str(record.get("branch") or "ordinary")
        draw_source = str(record.get("draw_source") or "random")
        flags = {item for item in str(record.get("story_flags") or "").split(",") if item}
        if branch == "mutual":
            encounter = f"{actor}和{target}几乎同时注意到{prop}，两个人都没有先把这次巧合说成偶然。"
        elif branch in {"contested", "popular"}:
            encounter = f"{target}已经被写进不止一段缘分里，但{actor}仍在{prop}旁留下了自己的位置。"
        elif "redraw" in flags:
            encounter = f"{actor}把上一页收好后，在{prop}旁遇见了{target}；这不是重来，而是新的开场。"
        elif draw_source == "directed":
            encounter = f"{actor}没有把这次相遇交给随机，而是在{prop}旁专门等到了{target}。"
        elif "reunion" in flags:
            encounter = f"{actor}和{target}曾在旧日相遇，今天又在{prop}旁认出了彼此。"
        else:
            encounter = f"{actor}先停在{prop}旁，{target}恰好也没有离开，于是这段关系有了第一幕。"
        arc = relation.get("narrative") if isinstance(relation.get("narrative"), Mapping) else {}
        hook = arc.get("hook") if isinstance(arc.get("hook"), Mapping) else {}
        hook_summary = str(hook.get("summary") or f"{prop}还在等一个回应")
        action_options = cls.action_options_for_arc(context, arc)
        available_actions = tuple(str(item["intent"]) for item in action_options)
        blocks = (
            {"label": "今日开场", "text": str(context["opening"])},
            {"label": "相遇", "text": encounter},
            {"label": "关系走向", "text": f"{hook_summary}。接下来的变化会在今天的三幕故事里自然发生。"},
        )
        return {
            "version": 3,
            "scene_id": str(context["scene_id"]),
            "title": str(context["title"]),
            "prop": prop,
            "relationship_label": str(record.get("relationship_key") or arc.get("relationship_label") or "今日同行者"),
            "blocks": blocks,
            "text": "\n".join(str(block["text"]) for block in blocks),
            "next_action": "",
            "available_actions": available_actions or ("靠近", "倾听"),
            "action_options": action_options,
            "mention_lead": f"《{str(context['title'])}》刚开场，{actor}在{prop}旁遇见了",
        }

    @classmethod
    def action_options_for_arc(
        cls,
        context: Mapping[str, Any],
        arc: Mapping[str, Any] | None,
        allowed_intents: Sequence[str] = ("靠近", "倾听"),
        *,
        relation_key: str = "",
        target_id: int | None = None,
        target_name: str = "",
        requires_mention: bool = False,
        allow_compatibility: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        """Expose the current beat as serializable, player-facing choices.

        The engine retains canonical intents for backward compatibility, while
        the choice ID and risk text make the selected route explicit.
        """

        options: list[dict[str, Any]] = []
        for intent in dict.fromkeys(str(item) for item in allowed_intents if str(item)):
            choice = cls.choice_for_arc(
                context,
                arc,
                intent,
                allow_compatibility=allow_compatibility,
            )
            if choice is None:
                continue
            command = f"#互动 {choice.command}"
            display_command = command
            if requires_mention:
                display_command = f"{command} + @{target_name or '目标群友'}"
            options.append(
                {
                    "choice_id": choice.id,
                    "label": choice.label,
                    "command": command,
                    "display_command": display_command,
                    "intent": choice.intent,
                    "risk_hint": choice.risk_hint,
                    "effect_profile": choice.effect_profile,
                    "relation_key": relation_key,
                    "requires_mention": bool(requires_mention),
                    "mentioned_id": int(target_id) if target_id else None,
                    "mentioned_nickname": str(target_name or ""),
                }
            )
        return tuple(options)

    @classmethod
    def choice_for_arc(
        cls,
        context: Mapping[str, Any],
        arc: Mapping[str, Any] | None,
        intent: str,
        choice_id: str = "",
        *,
        allow_compatibility: bool = True,
    ) -> StoryChoice | None:
        """Resolve a legacy action name to the current beat's concrete choice."""

        normalized_intent = str(intent or "").strip()
        normalized_choice_id = str(choice_id or "").strip()
        beat = cls._beat_for(context, cls.branch_state(context, arc).get("beat_id"))
        if beat is not None:
            for choice in beat.choices:
                if normalized_choice_id and choice.id == normalized_choice_id:
                    return choice
            for choice in beat.choices:
                if normalized_intent in {choice.intent, choice.command}:
                    return choice
            if not allow_compatibility:
                return None
        if not allow_compatibility:
            return None
        return cls._compatibility_choice(context, cls.branch_state(context, arc).get("beat_id"), normalized_intent)

    @classmethod
    def branch_state(
        cls,
        context: Mapping[str, Any],
        arc: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Read v2 and v3 relation arcs without mutating prompt-only reads."""

        source = arc.get("branch") if isinstance(arc, Mapping) and isinstance(arc.get("branch"), Mapping) else {}
        beat_id = str(source.get("beat_id") or (arc or {}).get("beat_id") or context.get("start_beat_id") or "opening")
        if cls._beat_for(context, beat_id) is None:
            beat_id = str(context.get("start_beat_id") or "opening")
        history = source.get("history") if isinstance(source, Mapping) else ()
        return {
            "beat_id": beat_id,
            "last_choice_id": str(source.get("last_choice_id") or "") if isinstance(source, Mapping) else "",
            "last_outcome_id": str(source.get("last_outcome_id") or "") if isinstance(source, Mapping) else "",
            "last_ending_id": str(source.get("last_ending_id") or "") if isinstance(source, Mapping) else "",
            "history": [dict(item) for item in history if isinstance(item, Mapping)] if isinstance(history, Sequence) and not isinstance(history, str) else [],
        }

    @classmethod
    def transition_for_choice(
        cls,
        context: Mapping[str, Any],
        arc: Mapping[str, Any] | None,
        intent: str,
        outcome: str,
        *,
        choice_id: str = "",
    ) -> tuple[StoryChoice, StoryTransition] | None:
        choice = cls.choice_for_arc(context, arc, intent, choice_id)
        if choice is None:
            return None
        transition = choice.transitions.get(str(outcome)) or choice.transitions.get("neutral")
        return (choice, transition) if transition is not None else None

    @classmethod
    def ending_text(
        cls,
        context: Mapping[str, Any],
        ending_id: str,
        seed: str,
        **values: str,
    ) -> str:
        endings_by_id = context.get("ending_by_id") if isinstance(context.get("ending_by_id"), Mapping) else {}
        template = str(endings_by_id.get(str(ending_id)) or "") if isinstance(endings_by_id, Mapping) else ""
        if not template:
            endings = tuple(str(item) for item in context.get("endings", ()) if str(item))
            template = cls._pick(endings, f"{seed}:ending") if endings else f"{str(context.get('prop') or '这条线索')}还留在原处。"
        values.setdefault("prop", str(context.get("prop") or "这条线索"))
        return cls._format(template, **values)

    @classmethod
    def _beat_for(cls, context: Mapping[str, Any], beat_id: Any) -> StoryBeat | None:
        beats = context.get("beats") if isinstance(context.get("beats"), Mapping) else {}
        beat = beats.get(str(beat_id)) if isinstance(beats, Mapping) else None
        return beat if isinstance(beat, StoryBeat) else None

    @classmethod
    def _compatibility_choice(cls, context: Mapping[str, Any], beat_id: Any, intent: str) -> StoryChoice | None:
        """Keep old commands usable when a newer beat does not author them."""

        profiles = {
            "靠近": ("主动靠近", "这一步更直接，可能把话推得太快", "bold", "shared_moment", {"留到明天": 2, "被接住": 1}),
            "倾听": ("认真倾听", "推进较慢，但会把节奏留给对方", "steady", "answer", {"被接住": 2, "留到明天": 1}),
            "回应": ("给出回应", "会让等待得到明确答案", "answer", "after_answer", {"被接住": 3}),
            "修复": ("把话说开", "需要承担一次没接住的瞬间", "repair", "after_answer", {"找回": 3}),
            "助攻": ("替人递回线索", "只能创造机会，结局仍由当事人决定", "assist", "shared_moment", {"被接住": 2}),
        }
        if intent not in profiles:
            return None
        label, risk_hint, effect_profile, positive_beat, positive_route = profiles[intent]
        prefix = f"{str(beat_id or 'opening')}_{intent}"
        return StoryChoice(
            id=prefix,
            label=label,
            command=intent,
            intent=intent,
            risk_hint=risk_hint,
            effect_profile=effect_profile,
            transitions={
                "positive": StoryTransition(f"{prefix}_positive", positive_beat, f"{{prop}}旁的这一步已经被看见，等下一次回应", dict(positive_route), "held_answer"),
                "negative": StoryTransition(f"{prefix}_negative", "repair", f"{{prop}}旁留下了还没说清的分岔", {"错过": 3}, "night_memory"),
                "neutral": StoryTransition(f"{prefix}_neutral", "shared_moment", f"{{prop}}还在原处，故事可以慢一点继续", {"留到明天": 2}, "new_page"),
            },
        )

    @classmethod
    def compose_interaction(
        cls,
        context: Mapping[str, Any],
        prepared: Mapping[str, Any],
        relation_states: Mapping[str, Mapping[str, Any]],
        seed: str,
    ) -> dict[str, Any]:
        """Advance the chosen script with explicit causal state transitions."""

        effects = tuple(effect for effect in prepared.get("effects", ()) if isinstance(effect, Mapping))
        actor = cls._name(prepared.get("actor_nickname"))
        kind = str(prepared.get("kind") or "same_scene")
        intent = str(prepared.get("intent") or "auto")
        choice_semantics = cls._choice_semantics(intent, kind)
        focus = effects[0] if effects else {}
        focus_key = cls._relation_key(focus)
        focus_state = relation_states.get(focus_key, {})
        focus_arc = (
            focus_state.get("narrative")
            if isinstance(focus_state.get("narrative"), Mapping)
            else {}
        )
        focus_hook = focus_arc.get("hook") if isinstance(focus_arc.get("hook"), Mapping) else {}
        focus_hook_summary = str(
            focus_hook.get("summary") or f"{str(context['prop'])}留下的线索"
        )
        previous_event_id = focus_hook.get("source_event_id") or focus_arc.get("previous_event_id")

        updates: dict[str, dict[str, Any]] = {}
        transition_details: dict[str, dict[str, Any]] = {}
        route_delta = {"找回": 0, "错过": 0, "被接住": 0, "留到明天": 0}
        for effect in effects:
            key = cls._relation_key(effect)
            current = relation_states.get(key, {})
            current_arc = (
                current.get("narrative")
                if isinstance(current.get("narrative"), Mapping)
                else {}
            )
            old_hook = (
                current_arc.get("hook")
                if isinstance(current_arc.get("hook"), Mapping)
                else {}
            )
            old_hook_summary = str(
                old_hook.get("summary") or f"{str(context['prop'])}留下的线索"
            )
            left = cls._name(effect.get("left") or actor)
            right = cls._name(
                effect.get("right") or prepared.get("mentioned_name") or "这位群友"
            )
            delta = int(effect.get("delta") or 0)
            before = int(effect.get("before_affection") or current.get("affection") or 0)
            after = int(effect.get("after_affection") or before + delta)
            mark = str(effect.get("mark") or "留下新的印记")
            outcome_key = "positive" if delta > 0 else "negative" if delta < 0 else "neutral"
            choice_id = str(prepared.get("choice_id") or "") if key == focus_key else ""
            selected = cls.transition_for_choice(
                context,
                current_arc,
                intent,
                outcome_key,
                choice_id=choice_id,
            )
            if selected is None:
                # Valid legacy commands still get a deterministic fallback even
                # when an old arc lacks the current beat graph.
                choice = cls._compatibility_choice(
                    context,
                    cls.branch_state(context, current_arc).get("beat_id"),
                    intent,
                )
                transition = (
                    choice.transitions.get(outcome_key)
                    if choice is not None
                    else None
                )
            else:
                choice, transition = selected
            if choice is None or transition is None:
                continue

            next_hook = cls._format(
                transition.next_hook,
                actor=actor,
                target=right,
                left=left,
                right=right,
                prop=str(context["prop"]),
            )
            if transition.next_beat_id == "repair":
                hook_kind = "repair"
            elif transition.next_beat_id in {"answer", "after_answer"}:
                hook_kind = "answer"
            else:
                hook_kind = "follow_up"
            source_event_id = None
            if delta >= 0 and kind == "chain" and old_hook.get("source_event_id") not in (None, ""):
                # A repair continues to point at the original negative event.
                source_event_id = old_hook.get("source_event_id")
            branch = cls.branch_state(context, current_arc)
            history = list(branch["history"])
            history.append(
                {
                    "event_id": None,
                    "hook_id": str(old_hook.get("id") or ""),
                    "choice_id": choice.id,
                    "outcome_id": transition.outcome_id,
                    "next_beat_id": transition.next_beat_id,
                }
            )
            effect_phase = cls._next_phase(kind, delta, after)
            hook = {
                "id": f"{transition.outcome_id}:{seed}:{key}",
                "kind": hook_kind,
                "summary": next_hook,
                "status": "open",
                "source_event_id": source_event_id,
            }
            recent = [str(item) for item in current_arc.get("recent_beat_ids", ()) if str(item)]
            updates[key] = {
                "version": 3,
                "scene_id": str(context["scene_id"]),
                "phase": effect_phase,
                "question": str(
                    current_arc.get("question")
                    or f"这段关系会怎样回应{str(context['prop'])}？"
                ),
                "facts": cls._next_facts(current_arc, context, effect, old_hook_summary),
                "beat_id": transition.next_beat_id,
                "hook": hook,
                "last_summary": "",
                "previous_event_id": None,
                "recent_beat_ids": (recent + [transition.outcome_id])[-6:],
                "relationship_label": str(
                    current_arc.get("relationship_label") or "今日同行者"
                ),
                "relationship_origin": str(
                    current_arc.get("relationship_origin") or "random"
                ),
                "branch": {
                    "beat_id": transition.next_beat_id,
                    "last_choice_id": choice.id,
                    "last_outcome_id": transition.outcome_id,
                    "last_ending_id": transition.ending_id,
                    "history": history[-12:],
                },
            }
            transition_details[key] = {
                "choice": choice,
                "transition": transition,
                "hook_summary": old_hook_summary,
                "next_hook": next_hook,
                "left": left,
                "right": right,
                "delta": delta,
                "before": before,
                "after": after,
                "mark": mark,
                "outcome_key": outcome_key,
            }
            for route_key, score in transition.route_delta.items():
                if route_key in route_delta:
                    route_delta[route_key] += int(score)

        focus_detail = transition_details.get(focus_key, {})
        left = str(focus_detail.get("left") or actor)
        right = str(focus_detail.get("right") or prepared.get("mentioned_name") or "这位群友")
        mark = str(focus_detail.get("mark") or "留下新的印记")
        delta = int(focus_detail.get("delta") or 0)
        before = int(focus_detail.get("before") or 0)
        after = int(focus_detail.get("after") or before + delta)
        outcome_key = str(focus_detail.get("outcome_key") or "neutral")
        choice = focus_detail.get("choice")
        transition = focus_detail.get("transition")
        next_hook = str(focus_detail.get("next_hook") or f"{str(context['prop'])}还在等下一次回应")
        action = cls._script_action(
            context,
            str(choice.intent if isinstance(choice, StoryChoice) else intent),
            kind,
            seed,
            actor=actor,
            target=right,
            left=left,
            right=right,
            hook=focus_hook_summary,
        )
        recap = ""
        if previous_event_id not in (None, "") and focus_hook_summary:
            recap = f"上一幕留下的“{focus_hook_summary}”仍在等待回应。"
        outcome_text = cls._script_outcome(
            context,
            "resolved" if kind == "chain" and delta >= 0 else outcome_key,
            seed,
            left=left,
            right=right,
            mark=mark,
        )
        if effects:
            consequence = (
                f"{outcome_text} 好感从 {before:+d} 变为 {after:+d}；"
                f"下一幕：{next_hook}。"
            )
        else:
            consequence = outcome_text
        if focus_key in updates:
            updates[focus_key]["last_summary"] = consequence

        blocks: list[dict[str, str]] = []
        if recap:
            blocks.append({"label": "承接上一幕", "text": recap})
        choice_label = choice.label if isinstance(choice, StoryChoice) else INTENT_LABELS.get(intent, "本次行动")
        blocks.append({"label": str(choice_label), "text": action})
        blocks.append({"label": "结果与下一步", "text": consequence})
        secondary_beats: list[dict[str, str]] = []
        for effect in effects[1:]:
            key = cls._relation_key(effect)
            detail = transition_details.get(key, {})
            effect_left = str(detail.get("left") or cls._name(effect.get("left")))
            effect_right = str(detail.get("right") or cls._name(effect.get("right")))
            effect_delta = int(detail.get("delta") or effect.get("delta") or 0)
            effect_mark = str(detail.get("mark") or effect.get("mark") or "新的变化")
            effect_outcome = cls._script_outcome(
                context,
                str(detail.get("outcome_key") or "neutral"),
                f"{seed}:side:{key}",
                left=effect_left,
                right=effect_right,
                mark=effect_mark,
            )
            next_text = str(detail.get("next_hook") or "这段关系还在等下一次回应")
            text = f"{effect_outcome} 好感变化 {effect_delta:+d}；下一幕：{next_text}。"
            secondary_beats.append({"relation": f"{effect_left} → {effect_right}", "text": text})
            blocks.append({"label": f"牵连｜{effect_left} → {effect_right}", "text": text})

        return {
            "version": 3,
            "scene_id": str(context["scene_id"]),
            "intent": intent,
            "intent_label": INTENT_LABELS.get(intent, "本次行动"),
            "focus_relation": focus_key,
            "choice_semantics": choice_semantics,
            "choice_id": choice.id if isinstance(choice, StoryChoice) else "",
            "outcome_id": transition.outcome_id if isinstance(transition, StoryTransition) else "",
            "next_beat_id": transition.next_beat_id if isinstance(transition, StoryTransition) else "",
            "next_hook": next_hook,
            "route_delta": route_delta,
            "ending_id": transition.ending_id if isinstance(transition, StoryTransition) else "",
            "callback_event_id": previous_event_id,
            "primary_beat": {"relation": f"{left} → {right}", "text": consequence},
            "secondary_beats": secondary_beats,
            "blocks": blocks,
            "text": "\n".join(str(block["text"]) for block in blocks),
            "beat_ids": [
                str(detail["transition"].outcome_id)
                for detail in transition_details.values()
                if isinstance(detail.get("transition"), StoryTransition)
            ],
            "relation_updates": updates,
        }

        blocks: list[dict[str, str]] = []
        if recap:
            blocks.append({"label": "承接上一幕", "text": recap})
        blocks.append({"label": INTENT_LABELS.get(intent, "本次行动"), "text": action})
        blocks.append({"label": "结果与下一步", "text": consequence})
        secondary_beats: list[dict[str, str]] = []
        for effect in effects[1:]:
            effect_left = cls._name(effect.get("left"))
            effect_right = cls._name(effect.get("right"))
            effect_delta = int(effect.get("delta") or 0)
            effect_mark = str(effect.get("mark") or "新的变化")
            effect_outcome = cls._script_outcome(
                context,
                "positive" if effect_delta > 0 else "negative" if effect_delta < 0 else "neutral",
                f"{seed}:side:{cls._relation_key(effect)}",
                left=effect_left,
                right=effect_right,
                mark=effect_mark,
            )
            text = f"{effect_outcome} 因此好感变化 {effect_delta:+d}。"
            secondary_beats.append({"relation": f"{effect_left} → {effect_right}", "text": text})
            blocks.append({"label": f"牵连｜{effect_left} → {effect_right}", "text": text})
        beat_id = f"{kind}:{intent}:{seed}"
        updates: dict[str, dict[str, Any]] = {}
        for effect in effects:
            key = cls._relation_key(effect)
            current = relation_states.get(key, {})
            current_arc = current.get("narrative") if isinstance(current.get("narrative"), Mapping) else {}
            effect_delta = int(effect.get("delta") or 0)
            effect_after = int(effect.get("after_affection") or int(current.get("affection") or 0) + effect_delta)
            effect_phase = cls._next_phase(kind, effect_delta, effect_after)
            effect_hook = hook if key == focus_key else {
                "id": f"side:{beat_id}:{key}",
                "kind": "ripple",
                "summary": f"{str(effect.get('mark') or '余波')}留在这段关系里",
                "status": "open" if effect_delta >= 0 else "open",
                "source_event_id": None,
            }
            recent = [str(item) for item in current_arc.get("recent_beat_ids", ()) if str(item)]
            updates[key] = {
                "version": 2,
                "scene_id": str(context["scene_id"]),
                "phase": effect_phase,
                "question": str(current_arc.get("question") or f"这段关系会怎样回应{str(context['prop'])}？"),
                "facts": cls._next_facts(current_arc, context, effect, old_hook_summary),
                "beat_id": beat_id,
                "hook": effect_hook,
                "last_summary": consequence if key == focus_key else f"{str(effect.get('mark') or '余波')}让关系发生了变化。",
                "previous_event_id": None,
                "recent_beat_ids": (recent + [beat_id])[-6:],
                "relationship_label": str(current_arc.get("relationship_label") or "今日同行者"),
            }
        return {
            "version": 2,
            "scene_id": str(context["scene_id"]),
            "intent": intent,
            "intent_label": INTENT_LABELS.get(intent, "本次行动"),
            "focus_relation": focus_key,
            "choice_semantics": choice_semantics,
            "callback_event_id": previous_event_id,
            "primary_beat": {"relation": f"{left} → {right}", "text": consequence},
            "secondary_beats": secondary_beats,
            "blocks": blocks,
            "text": "\n".join(str(block["text"]) for block in blocks),
            "beat_ids": [beat_id],
            "relation_updates": updates,
        }

    @classmethod
    def compose_conclusion(
        cls,
        context: Mapping[str, Any],
        events: Sequence[Mapping[str, Any]],
        seed: str,
        route_label: str = "留到明天",
        turning_point: str = "",
    ) -> str:
        if not events:
            ending = cls.ending_text(context, "", seed)
            return f"《{str(context['title'])}》以“{route_label}”落幕。{ending}"
        latest = events[-1]
        plan = latest.get("narrative_plan") if isinstance(latest.get("narrative_plan"), Mapping) else {}
        ending = cls.ending_text(context, str(plan.get("ending_id") or ""), seed)
        latest_hook = ""
        if plan:
            blocks = tuple(block for block in plan.get("blocks", ()) if isinstance(block, Mapping))
            if blocks:
                latest_hook = str(blocks[-1].get("text") or "")
        bridge = f"今天的转折是{turning_point}。" if turning_point else ""
        return f"《{str(context['title'])}》以“{route_label}”落幕。{bridge}{ending}{latest_hook}"

    @classmethod
    def group_caption(
        cls,
        context: Mapping[str, Any],
        records: Sequence[Mapping[str, Any]],
        events: Sequence[Mapping[str, Any]],
        act_title: str,
        route_label: str = "留到明天",
        turning_point: str = "",
    ) -> str:
        prop = str(context["prop"])
        if not records:
            return f"《{str(context['title'])}》刚刚亮灯，{prop}还在等第一位主角。"
        if events:
            turn = f" 转折是{turning_point}。" if turning_point else ""
            return f"《{str(context['title'])}》· {act_title}正在走向“{route_label}”。{len(records)} 段缘分围绕{prop}继续推进。{turn}"
        return f"《{str(context['title'])}》· {act_title}。{len(records)} 段缘分已经入场，{prop}正在等第一个回应。"

    @classmethod
    def relation_status_label(cls, relation: Mapping[str, Any]) -> str:
        if relation.get("frozen_affection") is not None:
            return "旧缘"
        arc = relation.get("narrative") if isinstance(relation.get("narrative"), Mapping) else {}
        phase = str(arc.get("phase") or "")
        if phase in PHASE_LABELS:
            return PHASE_LABELS[phase]
        affection = int(relation.get("affection") or 0)
        if affection < 0:
            return "需要修复"
        if affection >= 45:
            return "正在升温"
        return "微妙"

    @classmethod
    def _script_action(
        cls,
        context: Mapping[str, Any],
        intent: str,
        kind: str,
        seed: str,
        **values: str,
    ) -> str:
        choice_key = intent
        if kind == "chain":
            choice_key = "修复"
        elif kind == "response":
            choice_key = "回应"
        elif kind in {"assist", "interference"}:
            choice_key = "助攻"
        choices = context.get("choices") if isinstance(context.get("choices"), Mapping) else {}
        pool = choices.get(choice_key) if isinstance(choices, Mapping) else ()
        if not isinstance(pool, Sequence) or isinstance(pool, str) or not pool:
            pool = ("{actor}在{prop}旁停下来，决定先回应“{hook}”。",)
        values.setdefault("prop", str(context.get("prop") or "这条线索"))
        return cls._format(cls._pick(tuple(str(item) for item in pool), f"{seed}:{choice_key}"), **values)

    @classmethod
    def _script_outcome(cls, context: Mapping[str, Any], key: str, seed: str, **values: str) -> str:
        outcomes = context.get("outcomes") if isinstance(context.get("outcomes"), Mapping) else {}
        pool = outcomes.get(key) if isinstance(outcomes, Mapping) else ()
        if not isinstance(pool, Sequence) or isinstance(pool, str) or not pool:
            pool = ("这一幕留下了可以继续回应的线索。",)
        values.setdefault("prop", str(context.get("prop") or "这条线索"))
        return cls._format(cls._pick(tuple(str(item) for item in pool), f"{seed}:outcome:{key}"), **values)

    @staticmethod
    def _choice_semantics(intent: str, kind: str) -> str:
        if kind == "chain" or intent == "修复":
            return "repair"
        if kind == "response" or intent == "回应":
            return "respond"
        if kind in {"assist", "interference"} or intent == "助攻":
            return "assist"
        if intent == "倾听":
            return "listen"
        return "approach"

    @staticmethod
    def _next_facts(
        current_arc: Mapping[str, Any],
        context: Mapping[str, Any],
        effect: Mapping[str, Any],
        old_hook_summary: str,
    ) -> list[str]:
        facts = [str(item) for item in current_arc.get("facts", ()) if str(item)]
        if not facts:
            facts.append(str(context.get("setup_fact") or f"{str(context.get('prop') or '线索')}还在故事开始的位置。"))
        if old_hook_summary and old_hook_summary not in facts:
            facts.append(old_hook_summary)
        mark = str(effect.get("mark") or "新的变化")
        fact = f"{mark}发生在{str(context.get('prop') or '这条线索')}旁"
        if fact not in facts:
            facts.append(fact)
        return facts[-5:]

    @staticmethod
    def _next_phase(kind: str, delta: int, after: int) -> str:
        if delta < 0:
            return "fractured" if after < 0 else "awkward"
        if kind == "chain":
            return "repairing" if after < 25 else "warming"
        if kind == "response":
            return "answered"
        if delta >= 11 or after >= 45:
            return "warming"
        return "first_spark"

    @staticmethod
    def _format(template: str, **values: str) -> str:
        try:
            return str(template).format(**values)
        except (KeyError, ValueError):
            return str(template)

    @staticmethod
    def _pick(pool: Sequence[str], seed: str) -> str:
        if not pool:
            return ""
        digest = hashlib.blake2s(seed.encode("utf-8"), digest_size=8).digest()
        return str(pool[int.from_bytes(digest, "big") % len(pool)])

    @staticmethod
    def _record_seed(record: Mapping[str, Any], namespace: str) -> str:
        return ":".join(
            (
                namespace,
                str(record.get("group_id") or ""),
                str(record.get("day") or ""),
                str(record.get("actor_id") or ""),
                str(record.get("target_id") or ""),
                str(record.get("draw_index") or 1),
            )
        )

    @staticmethod
    def _relation_key(value: Mapping[str, Any]) -> str:
        return f"{int(value.get('actor_id') or 0)}:{int(value.get('draw_index') or 1)}"

    @staticmethod
    def _name(value: Any) -> str:
        return str(value or "这位群友").strip()[:40] or "这位群友"
