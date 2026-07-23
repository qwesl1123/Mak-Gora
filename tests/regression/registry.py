"""Aggregate scenario registry for the Mak'Gora regression suite.

SCENARIOS preserves the original tests/regression_suite.py run order across
all domain modules. validate_scenario_registry() fails when a scenario_*
function *defined* in a domain module is unregistered, when the same scenario
name is defined in more than one domain module, when a SCENARIOS entry is not
defined in one of the configured domain modules, when a registered entry does
not match (by identity) the discovered function of the same name, or when an
entry is registered twice, is not callable, or has a name that does not start
with scenario_. Discovery ignores scenario_* names merely imported into a
domain module by comparing each object's __module__ to the module it is found
in.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Callable, List, Tuple

from . import (
    test_resources,
    test_items_challenger,
    test_damage_pipeline,
    test_dots_hots,
    test_pets,
    test_effects_cc,
    test_ui_docs_metadata,
    test_classes_abilities,
)
from .test_resources import (
    scenario_on_hit_resource_gain_log_uses_actual_gained,
    scenario_queued_proc_resource_gain_uses_actual_dealt,
    scenario_recover_log_shows_only_nonzero_resources_and_uses_mana_wording,
    scenario_cleave_hits_all_targets_and_grants_rage_from_total_dealt,
    scenario_spirit_mana_regen_formula_and_class_baselines,
    scenario_spirit_end_of_turn_regen_is_silent_and_clamped,
    scenario_grant_player_resource_central_helper,
    scenario_apply_player_healing_helper_contract,
)
from .test_items_challenger import (
    scenario_rage_crystal_increases_all_rage_gain_sources,
    scenario_challengers_chestplate_resource_stance,
    scenario_challengers_chestplate_followup_fixes,
    scenario_challengers_chestplate_on_hit_proc_outgoing_stance,
    scenario_challengers_chestplate_wildfire_dot_outgoing_snapshot,
    scenario_item_passive_effect_panel_labels_and_descriptions,
    scenario_unstable_arcanocrystal_grants_expected_item_stats,
    scenario_unstable_arcanocrystal_documented_in_duel_html,
)
from .test_damage_pipeline import (
    scenario_healing_resolves_from_negative_hp_before_winner_check,
    scenario_partial_healing_keeps_hp_negative_until_winner_check,
    scenario_action_time_healing_applies_before_direct_damage,
    scenario_absorb_layering,
    scenario_aoe_resolves_targets_independently,
    scenario_winner_summary_logs_after_pet_phase_and_end_of_turn_resolution,
    scenario_phase_c_pass1_early_resolution_stages_are_preserved,
    scenario_phase_c_prompt1_middle_resolution_stages_are_preserved,
    scenario_immediate_path_denial_precedes_selection_failures,
    scenario_passive_secondary_damage_logs_own_absorb_suffix,
    scenario_dragonwrath_duplicate_spell_deals_real_damage,
    scenario_dragonwrath_duplicate_log_includes_class_owner_prefix,
    scenario_dragonwrath_multihit_duplicate_logs_as_single_line,
    scenario_passive_damage_event_preserves_multihit_instances_for_formatting_and_absorbs,
    scenario_damage_event_factories_build_normalized_plain_dicts,
    scenario_dragonwrath_duplicate_drain_life_heals_from_total_landed_damage,
    scenario_dragonwrath_duplicate_drain_life_does_not_heal_from_fully_absorbed_damage,
    scenario_drain_life_partial_absorb_heals_only_actual_hp_damage,
    scenario_fury_of_azzinoth_heal_from_dealt_includes_strike_again_damage,
    scenario_damage_derived_player_healing_routes_through_shared_helper,
    scenario_mindgames_converted_damage_is_not_credited_as_damage_done,
    scenario_thunderfury_lightning_uses_damage_pipeline,
    scenario_thunderfury_heal_proc_restores_expected_amount,
    scenario_azzinoth_strike_again_deals_secondary_damage,
    scenario_fury_of_azzinoth_cannot_miss_and_ignores_armor,
    scenario_mitigation_physical_uses_def_plus_armor,
    scenario_mitigation_magic_uses_def_plus_magic_resist,
    scenario_ignore_armor_bypasses_only_armor_component,
    scenario_pet_attacks_use_shared_mitigation_stats,
    scenario_break_on_damage_and_lifesteal_use_post_mitigation_damage,
    scenario_phase_c_prompt2_no_spillover_to_effect_application_or_end_of_turn,
    scenario_phase_c_prompt3_effect_application_stage_preserved,
    scenario_phase_d_end_of_turn_stage_preserved,
    scenario_subschool_metadata_and_templates,
    scenario_subschool_event_plumbing_for_dots_and_passives,
    scenario_direct_damage_dot_inherits_ability_subschool,
    scenario_true_aoe_school_subschool_propagation,
    scenario_nature_resistance_reduces_nature_damage,
    scenario_nature_resistance_no_cross_school_protection,
    scenario_subschool_resistance_additive_before_curve,
    scenario_subschool_resistance_is_generic_via_fire_resist,
    scenario_nature_resistance_applies_across_damage_sources,
    scenario_ignore_magic_resist_bypasses_subschool_resistance,
    scenario_subschool_resistance_compatibility_and_defaults,
    scenario_high_risk_shields_absorbs_regression_pack,
    scenario_step2_absorb_shield_contracts,
    scenario_high_risk_end_of_turn_lethal_ordering_pack,
    scenario_step2_end_of_turn_lethal_ordering_contracts,
    scenario_phase0_early_pipeline_contract_lock,
    scenario_phase0_absorb_shield_contract_lock,
    scenario_phase0_end_of_turn_ordering_contract_lock,
    scenario_phase0_normal_vs_immediate_parity_ordering_lock,
)
from .test_dots_hots import (
    scenario_hunter_wildfire_arcane_proc,
    scenario_hunter_wildfire_dot_log_order,
    scenario_mass_dispel_removes_same_turn_wildfire_burn,
    scenario_mindgames_still_allows_direct_damage_dots,
    scenario_mindgames_conversion_credits_caster_actual_gain_and_overheal,
    scenario_regen_healing_overhealing_and_negative_hp_accounting,
    scenario_devouring_plague_heals_for_full_tick_damage,
    scenario_end_of_turn_healing_applies_before_queued_dot_damage,
    scenario_passive_and_end_of_turn_player_healing_routes_through_shared_helper,
    scenario_ancestral_knowledge_temporary_nonpositive_hp_rules,
    scenario_ancestral_knowledge_cyclone_precedence,
    scenario_ancestral_knowledge_mindgames_self_damage_pipeline,
    scenario_agony_ramp_progression_restored,
    scenario_dot_balance_per_turn_values_and_durations,
    scenario_shaman_healing_stream_hot,
)
from .test_pets import (
    scenario_pet_summon_data_driven,
    scenario_pet_totem_runtime_normalization_phase1,
    scenario_pet_totem_runtime_normalization_phase2b,
    scenario_hunter_pet_summon_swap_memory,
    scenario_hunter_only_one_active_pet,
    scenario_hunter_companion_calls_have_no_cooldown,
    scenario_hunter_aimed_shot_raptor_pet_special,
    scenario_hunter_boar_redirect,
    scenario_hunter_boar_redirects_single_target_cc,
    scenario_hunter_boar_redirect_same_turn_brace,
    scenario_hunter_boar_forced_pre_action_redirect_is_consistent,
    scenario_hunter_boar_no_late_brace_without_redirect,
    scenario_hunter_raptor_strike_forces_boar_redirect,
    scenario_hunter_pet_permanent_death,
    scenario_hunter_pet_permanent_death_resummon_blocked,
    scenario_hunter_dead_pet_type_does_not_block_other_pet_types,
    scenario_hunter_dismissed_pet_clears_runtime_effects,
    scenario_hunter_multi_pet_memory_swap_cycle,
    scenario_hunter_pet_resource_memory_and_clamp,
    scenario_non_persistent_pet_memory_unchanged,
    scenario_hunter_redirect_removed_on_pet_dismiss,
    scenario_hunter_serpent_special_respects_stealth,
    scenario_pet_action_text_persists_on_miss,
    scenario_imp_firebolt_immunity_logs_under_cloak,
    scenario_imp_firebolt_target_check_ordering,
    scenario_pet_specials_are_blocked_while_pet_is_ccd,
    scenario_hunter_pet_recall_uses_calls_for_wording,
    scenario_pet_primary_resource_snapshot_contract,
    scenario_imp_firebolt_mana_cost_is_three,
    scenario_pet_incoming_physical_uses_centralized_mitigation_phase3,
    scenario_pet_incoming_magical_uses_centralized_resist_phase3,
    scenario_imp_and_shadowfiend_incoming_use_centralized_pet_mitigation_phase3,
    scenario_pet_totem_default_magic_resist_zero_phase3,
    scenario_entity_type_phase3_validation_suite,
    scenario_entity_type_phase3_completeness_audit,
    scenario_shadowfiend_summon_log_deduped,
    scenario_shaman_totems_and_astral_explosion,
    scenario_capacitor_totem_aoe_timing_and_duration,
    scenario_shaman_astral_explosion_no_pet_consumes_absorb,
    scenario_high_risk_pet_legality_and_protection_pack,
    scenario_step2_pet_legality_and_protection_contracts,
    scenario_phase0_pet_legality_and_protection_contract_lock,
    scenario_pet_attack_logs_on_miss_and_immune_consistently,
    scenario_pet_hot_tick_credits_owner_pet_healing_bucket,
)
from .test_effects_cc import (
    scenario_cloak_of_shadows_interactions,
    scenario_stealth_priority_over_stun,
    scenario_immunity_priority_over_stuns,
    scenario_stealth_priority_over_stuns_expanded,
    scenario_stun_priority_over_blink_like,
    scenario_blink_like_blocks_attacks_for_two_turns,
    scenario_iceblock_priority_vs_aoe_with_pets,
    scenario_blink_like_aoe_still_hits_pets,
    scenario_iceblock_blocks_same_turn_stun_and_next_turn_attack,
    scenario_aoe_hits_pets_with_immune_champion,
    scenario_hunter_turtle_priority,
    scenario_hunter_turtle_blocks_pet_spell_debuff_and_failed_cast_state,
    scenario_hunter_turtle_same_turn_psychic_scream_consistency,
    scenario_hunter_freezing_trap_breaks_on_damage,
    scenario_hunter_freezing_trap_respects_cloak_same_turn,
    scenario_hunter_freezing_trap_respects_active_cloak,
    scenario_divine_shield_lasts_three_turns,
    scenario_cyclone_lasts_three_turns_and_has_status_metadata,
    scenario_cyclone_denial_log_uses_cycloned_wording,
    scenario_mage_hot_streak_lasts_three_turns,
    scenario_ring_of_ice_freezes_and_breaks_on_damage,
    scenario_fear_applies_feared_and_breaks_on_damage,
    scenario_break_on_damage_cc_no_damage_turn_preserves_lockout,
    scenario_break_on_damage_cc_dot_tick_breaks,
    scenario_break_on_damage_cc_aoe_breaks,
    scenario_break_on_damage_cc_pet_damage_breaks,
    scenario_break_on_damage_cc_persists_after_same_turn_mutual_freeze,
    scenario_break_on_damage_cc_persists_after_same_turn_fear_vs_freeze,
    scenario_break_on_damage_logs_use_clean_wording_and_bottom_order,
    scenario_break_on_damage_uses_source_ability_name_for_shared_fear_state,
    scenario_redirected_damage_does_not_break_frozen,
    scenario_redirected_damage_does_not_break_feared,
    scenario_aoe_bypasses_redirect_and_breaks_frozen,
    scenario_dot_bypasses_redirect_and_breaks_feared,
    scenario_proc_raptor_strike_expires_correctly,
    scenario_proc_pyroblast_window_correct,
    scenario_negative_non_damage_effect_does_not_break_frozen,
    scenario_negative_non_damage_effect_does_not_break_feared,
    scenario_cc_status_display_metadata_is_exposed,
    scenario_key_buff_debuff_metadata_is_consistent,
    scenario_hunter_disengage_uses_custom_miss_text,
    scenario_blink_like_champion_status_and_disengage_duration,
    scenario_hunter_flare_logs_stealth_breaks,
    scenario_redirect_and_blink_like_coexist_without_cross_regression,
    scenario_mutual_stuns_count_current_turn_immediately,
    scenario_stealth_break_log_order_after_actions,
    scenario_mutual_freeze_duration_model_remains_unchanged,
    scenario_break_on_damage_cc_blocks_form_shift_same_turn,
    scenario_break_on_damage_cc_blocks_other_normal_actions_same_turn,
    scenario_only_selected_defensives_can_cast_while_crowd_controlled,
    scenario_stealth_breaks_on_total_turn_damage_threshold,
    scenario_earth_shock_duration_update_does_not_change_global_duration_semantics,
    scenario_proc_and_burn_duration_cleanup_and_shield_panel_cleanup,
    scenario_high_risk_same_turn_protection_and_denial_pack,
    scenario_high_risk_shared_effect_naming_and_panel_pack,
    scenario_phase0_same_turn_protection_and_denial_timing_lock,
)
from .test_ui_docs_metadata import (
    scenario_post_combat_summary_exposes_pet_healing_and_actual_damage_dpt,
    scenario_double_ko_post_combat_summary_renders_dpt_and_fight_length,
    scenario_warlock_imp_log_coloring_mapping_present,
    scenario_hunter_proc_log_stays_at_top_of_turn,
    scenario_proc_and_has_reminders_stay_in_expected_order,
    scenario_invalid_class_rejected,
    scenario_valid_class_id_is_normalized_before_build,
    scenario_prep_selection_name_uses_current_submission,
    scenario_command_input_normalizes_abilities_and_items,
    scenario_shadowfiend_pet_box_hides_turn_counter_badge,
    scenario_champion_mouseover_payload_contract,
    scenario_balance_metadata_updates_and_shadowstrike_rename,
    scenario_duel_html_agony_docs_updated,
    scenario_effect_panel_payload_normalization,
    scenario_high_risk_snapshot_payload_stability_pack,
)
from .test_classes_abilities import (
    scenario_mindgames_lay_on_hands,
    scenario_special_handler_healthstone_mindgames_parity,
    scenario_mindgames_converts_requested_pre_clamp_healing_near_cap,
    scenario_capped_action_healing_logs_report_actual_gain,
    scenario_action_time_player_healing_routes_through_shared_helper,
    scenario_generic_on_hit_healing_mindgames_backend,
    scenario_special_handler_innervate_mana_and_cooldown,
    scenario_special_handler_non_handler_baseline_path_unchanged,
    scenario_special_handler_mass_dispel_parity_and_denial_order,
    scenario_special_handler_holy_light_parity_and_denial_order,
    scenario_holy_light_near_cap_credits_actual_capped_healing,
    scenario_special_handler_flash_heal_parity_and_denial_order,
    scenario_special_handler_lay_on_hands_parity_and_denial_order,
    scenario_special_handler_frenzied_regeneration_parity_and_denial_order,
    scenario_special_handler_wild_growth_parity_and_denial_order,
    scenario_special_handler_regrowth_parity_and_denial_order,
    scenario_mindgames_shield_of_vengeance_explosion_interactions,
    scenario_mass_dispel_selective_removal,
    scenario_mass_dispel_can_remove_pain_suppression_and_devouring_plague,
    scenario_shield_of_vengeance_duration_counts_current_turn,
    scenario_hunter_multi_shot_aoe,
    scenario_dragon_roar_cannot_miss_from_accuracy,
    scenario_dragon_roar_bleed_applies_to_pets_with_independent_rolls,
    scenario_dragon_roar_dead_pets_do_not_log_bleed_application,
    scenario_hunter_rework_phase1_phase2_regression,
    scenario_kill_command_pet_heal_counted_once_in_totals,
    scenario_shadow_word_death_double_damage_reminder_wording,
    scenario_die_by_the_sword_log_wording,
    scenario_druid_form_requirement_log_wording,
    scenario_shield_of_vengeance_explosion_flushes_stealth_break_log,
    scenario_mind_blast_empowered_log_wording,
    scenario_priest_clarity_of_mind_buff_and_empowerment,
    scenario_shaman_shocks_apply_phase1_riders_and_lava_surge,
    scenario_shaman_same_turn_on_hit_rider_commitment_fairness,
    scenario_shaman_shock_and_lava_lash_balance_metadata,
    scenario_shaman_shock_lava_surge_proc_chances,
    scenario_shaman_shock_lava_surge_does_not_proc_on_no_hit,
    scenario_shaman_repeated_shock_lava_surge_stacks_and_logs,
    scenario_shaman_lava_lash_empowered_damage_and_consume,
    scenario_shaman_lava_surge_stackable_backend_contract,
    scenario_warrior_onslaught_stackable_contract,
    scenario_shaman_chain_lightning_aoe_and_docs_and_effect_panel,
    scenario_shaman_ancestral_guidance_and_knowledge,
    scenario_shaman_astral_shift_conversion,
    scenario_shaman_lightning_bolt_damage_and_shock_resets,
    scenario_shaman_and_rogue_docs_and_stats,
    scenario_paladin_divine_storm_behavior_and_docs,
    scenario_shield_of_vengeance_explosion_uses_absorbed_amount_for_pets,
    scenario_paladin_shield_of_vengeance_reset_and_no_unrelated_changes,
    scenario_empowered_by_metadata_validation,
    scenario_paladin_empowered_by_scaling_profiles,
    scenario_mind_blast_empowered_formula_consume_and_rng_order,
    scenario_empowerment_consumed_on_miss_but_not_on_rejection,
    scenario_lava_surge_and_flame_dance_stack_on_lava_lash,
)


_DOMAIN_MODULES = (
    test_resources,
    test_items_challenger,
    test_damage_pipeline,
    test_dots_hots,
    test_pets,
    test_effects_cc,
    test_ui_docs_metadata,
    test_classes_abilities,
)


SCENARIOS = [
    scenario_grant_player_resource_central_helper,
    scenario_apply_player_healing_helper_contract,
    scenario_mindgames_lay_on_hands,
    scenario_paladin_divine_storm_behavior_and_docs,
    scenario_shield_of_vengeance_explosion_uses_absorbed_amount_for_pets,
    scenario_paladin_shield_of_vengeance_reset_and_no_unrelated_changes,
    scenario_special_handler_healthstone_mindgames_parity,
    scenario_mindgames_converts_requested_pre_clamp_healing_near_cap,
    scenario_capped_action_healing_logs_report_actual_gain,
    scenario_action_time_player_healing_routes_through_shared_helper,
    scenario_generic_on_hit_healing_mindgames_backend,
    scenario_special_handler_innervate_mana_and_cooldown,
    scenario_special_handler_non_handler_baseline_path_unchanged,
    scenario_special_handler_mass_dispel_parity_and_denial_order,
    scenario_special_handler_holy_light_parity_and_denial_order,
    scenario_holy_light_near_cap_credits_actual_capped_healing,
    scenario_special_handler_flash_heal_parity_and_denial_order,
    scenario_special_handler_lay_on_hands_parity_and_denial_order,
    scenario_special_handler_frenzied_regeneration_parity_and_denial_order,
    scenario_special_handler_wild_growth_parity_and_denial_order,
    scenario_special_handler_regrowth_parity_and_denial_order,
    scenario_mindgames_shield_of_vengeance_explosion_interactions,
    scenario_mass_dispel_selective_removal,
    scenario_healing_resolves_from_negative_hp_before_winner_check,
    scenario_partial_healing_keeps_hp_negative_until_winner_check,
    scenario_action_time_healing_applies_before_direct_damage,
    scenario_mass_dispel_can_remove_pain_suppression_and_devouring_plague,
    scenario_cloak_of_shadows_interactions,
    scenario_shield_of_vengeance_duration_counts_current_turn,
    scenario_stealth_priority_over_stun,
    scenario_immunity_priority_over_stuns,
    scenario_stealth_priority_over_stuns_expanded,
    scenario_stun_priority_over_blink_like,
    scenario_blink_like_blocks_attacks_for_two_turns,
    scenario_iceblock_priority_vs_aoe_with_pets,
    scenario_blink_like_aoe_still_hits_pets,
    scenario_iceblock_blocks_same_turn_stun_and_next_turn_attack,
    scenario_aoe_hits_pets_with_immune_champion,
    scenario_rage_crystal_increases_all_rage_gain_sources,
    scenario_challengers_chestplate_resource_stance,
    scenario_challengers_chestplate_followup_fixes,
    scenario_challengers_chestplate_on_hit_proc_outgoing_stance,
    scenario_on_hit_resource_gain_log_uses_actual_gained,
    scenario_queued_proc_resource_gain_uses_actual_dealt,
    scenario_challengers_chestplate_wildfire_dot_outgoing_snapshot,
    scenario_item_passive_effect_panel_labels_and_descriptions,
    scenario_unstable_arcanocrystal_grants_expected_item_stats,
    scenario_unstable_arcanocrystal_documented_in_duel_html,
    scenario_absorb_layering,
    scenario_pet_summon_data_driven,
    scenario_pet_totem_runtime_normalization_phase1,
    scenario_pet_totem_runtime_normalization_phase2b,
    scenario_hunter_pet_summon_swap_memory,
    scenario_hunter_only_one_active_pet,
    scenario_hunter_companion_calls_have_no_cooldown,
    scenario_hunter_multi_shot_aoe,
    scenario_aoe_resolves_targets_independently,
    scenario_dragon_roar_cannot_miss_from_accuracy,
    scenario_dragon_roar_bleed_applies_to_pets_with_independent_rolls,
    scenario_dragon_roar_dead_pets_do_not_log_bleed_application,
    scenario_hunter_turtle_priority,
    scenario_hunter_turtle_blocks_pet_spell_debuff_and_failed_cast_state,
    scenario_hunter_turtle_same_turn_psychic_scream_consistency,
    scenario_hunter_wildfire_arcane_proc,
    scenario_hunter_rework_phase1_phase2_regression,
    scenario_kill_command_pet_heal_counted_once_in_totals,
    scenario_hunter_wildfire_dot_log_order,
    scenario_mass_dispel_removes_same_turn_wildfire_burn,
    scenario_hunter_proc_log_stays_at_top_of_turn,
    scenario_proc_and_has_reminders_stay_in_expected_order,
    scenario_recover_log_shows_only_nonzero_resources_and_uses_mana_wording,
    scenario_hunter_aimed_shot_raptor_pet_special,
    scenario_hunter_boar_redirect,
    scenario_hunter_boar_redirects_single_target_cc,
    scenario_hunter_boar_redirect_same_turn_brace,
    scenario_winner_summary_logs_after_pet_phase_and_end_of_turn_resolution,
    scenario_shadow_word_death_double_damage_reminder_wording,
    scenario_hunter_boar_forced_pre_action_redirect_is_consistent,
    scenario_hunter_boar_no_late_brace_without_redirect,
    scenario_hunter_raptor_strike_forces_boar_redirect,
    scenario_hunter_freezing_trap_breaks_on_damage,
    scenario_hunter_freezing_trap_respects_cloak_same_turn,
    scenario_hunter_freezing_trap_respects_active_cloak,
    scenario_die_by_the_sword_log_wording,
    scenario_druid_form_requirement_log_wording,
    scenario_divine_shield_lasts_three_turns,
    scenario_cyclone_lasts_three_turns_and_has_status_metadata,
    scenario_cyclone_denial_log_uses_cycloned_wording,
    scenario_mage_hot_streak_lasts_three_turns,
    scenario_ring_of_ice_freezes_and_breaks_on_damage,
    scenario_fear_applies_feared_and_breaks_on_damage,
    scenario_mutual_stuns_count_current_turn_immediately,
    scenario_phase_c_pass1_early_resolution_stages_are_preserved,
    scenario_phase_c_prompt1_middle_resolution_stages_are_preserved,
    scenario_agony_ramp_progression_restored,
    scenario_immediate_path_denial_precedes_selection_failures,
    scenario_mutual_freeze_duration_model_remains_unchanged,
    scenario_break_on_damage_cc_no_damage_turn_preserves_lockout,
    scenario_break_on_damage_cc_dot_tick_breaks,
    scenario_break_on_damage_cc_aoe_breaks,
    scenario_break_on_damage_cc_pet_damage_breaks,
    scenario_break_on_damage_cc_blocks_form_shift_same_turn,
    scenario_break_on_damage_cc_blocks_other_normal_actions_same_turn,
    scenario_only_selected_defensives_can_cast_while_crowd_controlled,
    scenario_break_on_damage_cc_persists_after_same_turn_mutual_freeze,
    scenario_break_on_damage_cc_persists_after_same_turn_fear_vs_freeze,
    scenario_break_on_damage_logs_use_clean_wording_and_bottom_order,
    scenario_break_on_damage_uses_source_ability_name_for_shared_fear_state,
    scenario_stealth_break_log_order_after_actions,
    scenario_shield_of_vengeance_explosion_flushes_stealth_break_log,
    scenario_redirected_damage_does_not_break_frozen,
    scenario_redirected_damage_does_not_break_feared,
    scenario_aoe_bypasses_redirect_and_breaks_frozen,
    scenario_dot_bypasses_redirect_and_breaks_feared,
    scenario_proc_raptor_strike_expires_correctly,
    scenario_proc_pyroblast_window_correct,
    scenario_cc_status_display_metadata_is_exposed,
    scenario_key_buff_debuff_metadata_is_consistent,
    scenario_hunter_disengage_uses_custom_miss_text,
    scenario_blink_like_champion_status_and_disengage_duration,
    scenario_hunter_flare_logs_stealth_breaks,
    scenario_hunter_pet_permanent_death,
    scenario_hunter_pet_permanent_death_resummon_blocked,
    scenario_hunter_dead_pet_type_does_not_block_other_pet_types,
    scenario_hunter_dismissed_pet_clears_runtime_effects,
    scenario_hunter_multi_pet_memory_swap_cycle,
    scenario_hunter_pet_resource_memory_and_clamp,
    scenario_non_persistent_pet_memory_unchanged,
    scenario_hunter_redirect_removed_on_pet_dismiss,
    scenario_hunter_pet_recall_uses_calls_for_wording,
    scenario_negative_non_damage_effect_does_not_break_frozen,
    scenario_negative_non_damage_effect_does_not_break_feared,
    scenario_hunter_serpent_special_respects_stealth,
    scenario_pet_action_text_persists_on_miss,
    scenario_imp_firebolt_immunity_logs_under_cloak,
    scenario_imp_firebolt_target_check_ordering,
    scenario_redirect_and_blink_like_coexist_without_cross_regression,
    scenario_pet_specials_are_blocked_while_pet_is_ccd,
    scenario_shadowfiend_pet_box_hides_turn_counter_badge,
    scenario_pet_primary_resource_snapshot_contract,
    scenario_imp_firebolt_mana_cost_is_three,
    scenario_mindgames_still_allows_direct_damage_dots,
    scenario_mindgames_conversion_credits_caster_actual_gain_and_overheal,
    scenario_regen_healing_overhealing_and_negative_hp_accounting,
    scenario_devouring_plague_heals_for_full_tick_damage,
    scenario_end_of_turn_healing_applies_before_queued_dot_damage,
    scenario_passive_and_end_of_turn_player_healing_routes_through_shared_helper,
    scenario_ancestral_knowledge_temporary_nonpositive_hp_rules,
    scenario_ancestral_knowledge_cyclone_precedence,
    scenario_ancestral_knowledge_mindgames_self_damage_pipeline,
    scenario_passive_secondary_damage_logs_own_absorb_suffix,
    scenario_dragonwrath_duplicate_spell_deals_real_damage,
    scenario_dragonwrath_duplicate_log_includes_class_owner_prefix,
    scenario_dragonwrath_multihit_duplicate_logs_as_single_line,
    scenario_passive_damage_event_preserves_multihit_instances_for_formatting_and_absorbs,
    scenario_damage_event_factories_build_normalized_plain_dicts,
    scenario_dragonwrath_duplicate_drain_life_heals_from_total_landed_damage,
    scenario_dragonwrath_duplicate_drain_life_does_not_heal_from_fully_absorbed_damage,
    scenario_drain_life_partial_absorb_heals_only_actual_hp_damage,
    scenario_thunderfury_lightning_uses_damage_pipeline,
    scenario_thunderfury_heal_proc_restores_expected_amount,
    scenario_azzinoth_strike_again_deals_secondary_damage,
    scenario_fury_of_azzinoth_heal_from_dealt_includes_strike_again_damage,
    scenario_damage_derived_player_healing_routes_through_shared_helper,
    scenario_mindgames_converted_damage_is_not_credited_as_damage_done,
    scenario_fury_of_azzinoth_cannot_miss_and_ignores_armor,
    scenario_mitigation_physical_uses_def_plus_armor,
    scenario_mitigation_magic_uses_def_plus_magic_resist,
    scenario_ignore_armor_bypasses_only_armor_component,
    scenario_pet_attacks_use_shared_mitigation_stats,
    scenario_pet_incoming_physical_uses_centralized_mitigation_phase3,
    scenario_pet_incoming_magical_uses_centralized_resist_phase3,
    scenario_imp_and_shadowfiend_incoming_use_centralized_pet_mitigation_phase3,
    scenario_pet_totem_default_magic_resist_zero_phase3,
    scenario_break_on_damage_and_lifesteal_use_post_mitigation_damage,
    scenario_phase_c_prompt2_no_spillover_to_effect_application_or_end_of_turn,
    scenario_phase_c_prompt3_effect_application_stage_preserved,
    scenario_phase_d_end_of_turn_stage_preserved,
    scenario_entity_type_phase3_validation_suite,
    scenario_entity_type_phase3_completeness_audit,
    scenario_champion_mouseover_payload_contract,
    scenario_subschool_metadata_and_templates,
    scenario_subschool_event_plumbing_for_dots_and_passives,
    scenario_direct_damage_dot_inherits_ability_subschool,
    scenario_true_aoe_school_subschool_propagation,
    scenario_nature_resistance_reduces_nature_damage,
    scenario_nature_resistance_no_cross_school_protection,
    scenario_subschool_resistance_additive_before_curve,
    scenario_subschool_resistance_is_generic_via_fire_resist,
    scenario_nature_resistance_applies_across_damage_sources,
    scenario_ignore_magic_resist_bypasses_subschool_resistance,
    scenario_subschool_resistance_compatibility_and_defaults,
    scenario_dot_balance_per_turn_values_and_durations,
    scenario_cleave_hits_all_targets_and_grants_rage_from_total_dealt,
    scenario_stealth_breaks_on_total_turn_damage_threshold,
    scenario_mind_blast_empowered_log_wording,
    scenario_shadowfiend_summon_log_deduped,
    scenario_balance_metadata_updates_and_shadowstrike_rename,
    scenario_duel_html_agony_docs_updated,
    scenario_priest_clarity_of_mind_buff_and_empowerment,
    scenario_shaman_shocks_apply_phase1_riders_and_lava_surge,
    scenario_shaman_same_turn_on_hit_rider_commitment_fairness,
    scenario_shaman_shock_and_lava_lash_balance_metadata,
    scenario_shaman_shock_lava_surge_proc_chances,
    scenario_shaman_shock_lava_surge_does_not_proc_on_no_hit,
    scenario_shaman_repeated_shock_lava_surge_stacks_and_logs,
    scenario_shaman_lava_lash_empowered_damage_and_consume,
    scenario_shaman_lava_surge_stackable_backend_contract,
    scenario_warrior_onslaught_stackable_contract,
    scenario_shaman_chain_lightning_aoe_and_docs_and_effect_panel,
    scenario_earth_shock_duration_update_does_not_change_global_duration_semantics,
    scenario_shaman_healing_stream_hot,
    scenario_shaman_ancestral_guidance_and_knowledge,
    scenario_shaman_astral_shift_conversion,
    scenario_shaman_lightning_bolt_damage_and_shock_resets,
    scenario_shaman_totems_and_astral_explosion,
    scenario_capacitor_totem_aoe_timing_and_duration,
    scenario_shaman_astral_explosion_no_pet_consumes_absorb,
    scenario_spirit_mana_regen_formula_and_class_baselines,
    scenario_spirit_end_of_turn_regen_is_silent_and_clamped,
    scenario_shaman_and_rogue_docs_and_stats,
    scenario_post_combat_summary_exposes_pet_healing_and_actual_damage_dpt,
    scenario_double_ko_post_combat_summary_renders_dpt_and_fight_length,
    scenario_effect_panel_payload_normalization,
    scenario_proc_and_burn_duration_cleanup_and_shield_panel_cleanup,
    scenario_high_risk_shields_absorbs_regression_pack,
    scenario_step2_absorb_shield_contracts,
    scenario_high_risk_end_of_turn_lethal_ordering_pack,
    scenario_step2_end_of_turn_lethal_ordering_contracts,
    scenario_high_risk_same_turn_protection_and_denial_pack,
    scenario_high_risk_pet_legality_and_protection_pack,
    scenario_step2_pet_legality_and_protection_contracts,
    scenario_high_risk_shared_effect_naming_and_panel_pack,
    scenario_high_risk_snapshot_payload_stability_pack,
    scenario_phase0_early_pipeline_contract_lock,
    scenario_phase0_same_turn_protection_and_denial_timing_lock,
    scenario_phase0_absorb_shield_contract_lock,
    scenario_phase0_end_of_turn_ordering_contract_lock,
    scenario_phase0_pet_legality_and_protection_contract_lock,
    scenario_pet_attack_logs_on_miss_and_immune_consistently,
    scenario_pet_hot_tick_credits_owner_pet_healing_bucket,
    scenario_phase0_normal_vs_immediate_parity_ordering_lock,
    scenario_invalid_class_rejected,
    scenario_valid_class_id_is_normalized_before_build,
    scenario_prep_selection_name_uses_current_submission,
    scenario_command_input_normalizes_abilities_and_items,
    scenario_warlock_imp_log_coloring_mapping_present,
    scenario_empowered_by_metadata_validation,
    scenario_paladin_empowered_by_scaling_profiles,
    scenario_mind_blast_empowered_formula_consume_and_rng_order,
    scenario_empowerment_consumed_on_miss_but_not_on_rejection,
    scenario_lava_surge_and_flame_dance_stack_on_lava_lash,
]


def _discover_scenario_functions() -> dict[str, Callable[..., Any]]:
    """Return scenario_* functions *defined* in the configured domain modules.

    Only functions whose ``__module__`` matches the module they are found in
    are discovered, so scenario_* names merely imported into a domain module
    (re-exports, cross-module imports) are ignored. A scenario name defined in
    more than one domain module is a duplicate definition and fails clearly.
    """
    discovered: dict[str, Callable[..., Any]] = {}
    defined_in: dict[str, list[str]] = {}
    for module in _DOMAIN_MODULES:
        for name, obj in vars(module).items():
            if not name.startswith("scenario_") or not callable(obj):
                continue
            if getattr(obj, "__module__", None) != module.__name__:
                continue
            discovered[name] = obj
            defined_in.setdefault(name, []).append(module.__name__)

    duplicate_definitions = {
        name: modules for name, modules in defined_in.items() if len(modules) > 1
    }
    if duplicate_definitions:
        detail = "; ".join(
            f"{name} defined in {', '.join(modules)}"
            for name, modules in sorted(duplicate_definitions.items())
        )
        raise AssertionError(
            "Scenario registry validation failed: duplicate scenario definitions "
            "across domain modules: " + detail
        )

    return discovered


def validate_scenario_registry() -> None:
    discovered = _discover_scenario_functions()
    registered_names: list[str] = []
    invalid_entries: list[str] = []
    identity_mismatches: list[str] = []

    for index, scenario in enumerate(SCENARIOS, start=1):
        name = getattr(scenario, "__name__", repr(scenario))
        registered_names.append(name)
        if not callable(scenario) or not name.startswith("scenario_"):
            invalid_entries.append(f"#{index}: {name}")
            continue
        # A registered entry must be the exact discovered function, not merely a
        # different callable that happens to share the same __name__.
        if name in discovered and scenario is not discovered[name]:
            identity_mismatches.append(name)

    duplicate_names = sorted(name for name, count in Counter(registered_names).items() if count > 1)
    missing_names = sorted(set(discovered) - set(registered_names))
    unknown_names = sorted(
        name
        for name in set(registered_names)
        if name.startswith("scenario_") and name not in discovered
    )
    identity_mismatch_names = sorted(set(identity_mismatches))

    failures: list[str] = []
    if missing_names:
        failures.append("unregistered scenario_* functions: " + ", ".join(missing_names))
    if unknown_names:
        failures.append(
            "SCENARIOS entries not defined in configured domain modules: "
            + ", ".join(unknown_names)
        )
    if identity_mismatch_names:
        failures.append(
            "SCENARIOS entries do not match discovered domain functions: "
            + ", ".join(identity_mismatch_names)
        )
    if duplicate_names:
        failures.append("duplicate SCENARIOS entries: " + ", ".join(duplicate_names))
    if invalid_entries:
        failures.append("non-callable or non-scenario SCENARIOS entries: " + ", ".join(invalid_entries))

    if failures:
        raise AssertionError("Scenario registry validation failed: " + "; ".join(failures))


def get_scenario_count() -> int:
    validate_scenario_registry()
    return len(SCENARIOS)


def run_all() -> List[Tuple[str, bool, str]]:
    validate_scenario_registry()
    results: List[Tuple[str, bool, str]] = []
    for scenario in SCENARIOS:
        try:
            scenario()
            results.append((scenario.__name__, True, ""))
        except AssertionError as exc:
            results.append((scenario.__name__, False, str(exc)))
    return results
