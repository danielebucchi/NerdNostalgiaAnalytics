from src.utils.condition import (
    CardCondition,
    RAW_GRADE_LABEL,
    RAW_GRADE_SCORE,
    RAW_GRADES,
    card_condition_emoji,
    card_condition_from_label,
    card_condition_to_pc_bucket,
    detect_card_condition,
)


class TestGraded:
    def test_psa_10(self):
        c = detect_card_condition("Charizard Base Set PSA 10 Gem Mint")
        assert c.is_graded
        assert c.grading_company == "PSA"
        assert c.grade == 10.0
        assert c.display == "PSA 10"

    def test_psa_no_space(self):
        c = detect_card_condition("Pikachu Holo PSA10")
        assert c.is_graded
        assert c.grading_company == "PSA"
        assert c.grade == 10.0

    def test_bgs_half_point(self):
        c = detect_card_condition("Blastoise BGS 9.5")
        assert c.is_graded
        assert c.grading_company == "BGS"
        assert c.grade == 9.5

    def test_bgs_comma_decimal(self):
        # European-style decimal separator
        c = detect_card_condition("Mewtwo BGS 9,5")
        assert c.is_graded
        assert c.grade == 9.5

    def test_cgc(self):
        c = detect_card_condition("Charizard CGC 8.5")
        assert c.is_graded
        assert c.grading_company == "CGC"
        assert c.grade == 8.5

    def test_beckett_normalised_to_bgs(self):
        c = detect_card_condition("Vintage card Beckett 9")
        assert c.is_graded
        assert c.grading_company == "BGS"
        assert c.grade == 9.0

    def test_sgc(self):
        c = detect_card_condition("Card SGC 8")
        assert c.is_graded
        assert c.grading_company == "SGC"
        assert c.grade == 8.0

    def test_case_insensitive(self):
        c = detect_card_condition("charizard psa 9")
        assert c.is_graded
        assert c.grading_company == "PSA"
        assert c.grade == 9.0

    def test_dash_separator(self):
        c = detect_card_condition("Charizard PSA-10")
        assert c.is_graded
        assert c.grade == 10.0

    def test_grade_clamped_to_half_points(self):
        # 9.3 should snap to 9.5 (closest .5 increment)
        c = detect_card_condition("PSA 9.3")
        assert c.is_graded
        assert c.grade == 9.5

    def test_out_of_range_rejected(self):
        c = detect_card_condition("PSA 15 fake")
        # 15 is out of range → graded path rejected → falls through to raw
        assert not c.is_graded
        # No raw signal either
        assert c.raw_grade is None


class TestRawFullPhrases:
    def test_near_mint(self):
        c = detect_card_condition("Charizard Base Set Near Mint")
        assert c.raw_grade == "NM"
        assert not c.is_graded
        assert c.display == "Raw NM (Near Mint)"

    def test_near_mint_with_dash(self):
        assert detect_card_condition("card Near-Mint shape").raw_grade == "NM"

    def test_lightly_played_beats_played(self):
        # "lightly played" must match LP, not PL
        c = detect_card_condition("Charizard Lightly Played")
        assert c.raw_grade == "LP"

    def test_light_played(self):
        assert detect_card_condition("Charizard Light Played").raw_grade == "LP"

    def test_excellent(self):
        assert detect_card_condition("Mewtwo Excellent condition").raw_grade == "EX"

    def test_good_condition(self):
        assert detect_card_condition("Blastoise good condition").raw_grade == "GO"

    def test_played(self):
        assert detect_card_condition("Pikachu played, some wear").raw_grade == "PL"

    def test_poor(self):
        assert detect_card_condition("Card heavily damaged, poor condition").raw_grade == "PO"

    def test_italian_perfetto_stato(self):
        assert detect_card_condition("Carta in perfetto stato").raw_grade == "NM"

    def test_italian_eccellente(self):
        assert detect_card_condition("Carta eccellente").raw_grade == "EX"

    def test_italian_leggermente_giocata(self):
        assert detect_card_condition("Carta leggermente giocata").raw_grade == "LP"

    def test_italian_giocata(self):
        assert detect_card_condition("Carta giocata, qualche segno").raw_grade == "PL"

    def test_italian_rovinata(self):
        assert detect_card_condition("Carta rovinata").raw_grade == "PO"

    def test_italian_buono_stato(self):
        assert detect_card_condition("Carta in buono stato").raw_grade == "GO"


class TestAbbreviations:
    def test_nm_in_parens(self):
        assert detect_card_condition("Charizard Base Set (NM)").raw_grade == "NM"

    def test_nm_slash_m(self):
        assert detect_card_condition("Card NM/M condition").raw_grade == "NM"

    def test_nm_with_dashes(self):
        assert detect_card_condition("Pikachu - NM - holo").raw_grade == "NM"

    def test_lp_in_brackets(self):
        assert detect_card_condition("Charizard [LP]").raw_grade == "LP"

    def test_sp_maps_to_lp(self):
        # Slightly Played ≈ Light Played
        assert detect_card_condition("Card SP, mint corners").raw_grade == "LP"

    def test_mp_maps_to_pl(self):
        # Moderately Played → Played
        assert detect_card_condition("Card MP, edge wear").raw_grade == "PL"

    def test_hp_maps_to_po(self):
        # Heavily Played → Poor
        assert detect_card_condition("Card HP heavy wear").raw_grade == "PO"

    def test_ex_plus(self):
        assert detect_card_condition("Card EX+ corners sharp").raw_grade == "EX"

    def test_nm_not_matched_inside_word(self):
        # "PHENOMENAL" contains "NM" inside, must not match
        assert detect_card_condition("Phenomenal Charizard card").raw_grade is None


class TestPriority:
    def test_graded_beats_raw(self):
        # Both "PSA 10" and "Near Mint" present — graded wins
        c = detect_card_condition("Charizard Near Mint PSA 10")
        assert c.is_graded
        assert c.grade == 10.0

    def test_full_phrase_beats_abbreviation(self):
        # "Lightly Played" should win over a trailing "(NM)" mention
        c = detect_card_condition("Charizard Lightly Played (NM border)")
        assert c.raw_grade == "LP"


class TestCardCondition:
    def test_is_known_graded(self):
        c = CardCondition(is_graded=True, grading_company="PSA", grade=10.0)
        assert c.is_known
        assert c.display == "PSA 10"

    def test_is_known_raw(self):
        c = CardCondition(raw_grade="NM")
        assert c.is_known
        assert c.display == "Raw NM (Near Mint)"

    def test_unknown(self):
        c = CardCondition()
        assert not c.is_known
        assert c.display == "Unknown"

    def test_quality_score_graded(self):
        assert CardCondition(is_graded=True, grading_company="PSA", grade=10.0).quality_score == 100
        assert CardCondition(is_graded=True, grading_company="PSA", grade=9.5).quality_score == 95
        assert CardCondition(is_graded=True, grading_company="PSA", grade=1.0).quality_score == 10

    def test_quality_score_raw(self):
        assert CardCondition(raw_grade="NM").quality_score == 60
        assert CardCondition(raw_grade="EX").quality_score == 50
        assert CardCondition(raw_grade="GO").quality_score == 40
        assert CardCondition(raw_grade="LP").quality_score == 30
        assert CardCondition(raw_grade="PL").quality_score == 20
        assert CardCondition(raw_grade="PO").quality_score == 10

    def test_quality_score_unknown(self):
        assert CardCondition().quality_score == 0

    def test_graded_ranks_above_raw(self):
        # Any graded card (even PSA 7) outranks the best raw (NM)
        assert (
            CardCondition(is_graded=True, grading_company="PSA", grade=7.0).quality_score
            > CardCondition(raw_grade="NM").quality_score
        )

    def test_raw_grades_strictly_ordered(self):
        scores = [RAW_GRADE_SCORE[g] for g in RAW_GRADES]
        # RAW_GRADES is declared best→worst
        assert scores == sorted(scores, reverse=True)

    def test_all_raw_grades_have_labels(self):
        for g in RAW_GRADES:
            assert g in RAW_GRADE_LABEL


class TestCanonicalLabels:
    """Mapping from Cardmarket/CardTrader canonical labels to CardCondition."""

    def test_near_mint_label(self):
        assert card_condition_from_label("Near Mint").raw_grade == "NM"

    def test_mint_collapses_to_nm(self):
        # We don't keep Mint distinct from NM in our 6-tier scale.
        assert card_condition_from_label("Mint").raw_grade == "NM"

    def test_excellent_label(self):
        assert card_condition_from_label("Excellent").raw_grade == "EX"

    def test_good_label(self):
        assert card_condition_from_label("Good").raw_grade == "GO"

    def test_light_played_label(self):
        assert card_condition_from_label("Light Played").raw_grade == "LP"

    def test_played_label(self):
        assert card_condition_from_label("Played").raw_grade == "PL"

    def test_poor_label(self):
        assert card_condition_from_label("Poor").raw_grade == "PO"

    def test_heavily_played_collapses_to_po(self):
        assert card_condition_from_label("Heavily Played").raw_grade == "PO"

    def test_none_label(self):
        assert not card_condition_from_label(None).is_known

    def test_empty_label(self):
        assert not card_condition_from_label("").is_known

    def test_unknown_label_falls_back_to_freetext(self):
        # "PSA 10" doesn't match the canonical map but falls back to detection.
        cc = card_condition_from_label("Charizard PSA 10")
        assert cc.is_graded
        assert cc.grade == 10.0


class TestPriceChartingBucket:
    def test_graded_to_psa_bucket(self):
        cc = CardCondition(is_graded=True, grading_company="PSA", grade=10.0)
        assert card_condition_to_pc_bucket(cc) == "Graded (PSA)"

    def test_raw_to_ungraded(self):
        for grade in RAW_GRADES:
            cc = CardCondition(raw_grade=grade)
            assert card_condition_to_pc_bucket(cc) == "Ungraded"

    def test_unknown_to_ungraded(self):
        assert card_condition_to_pc_bucket(CardCondition()) == "Ungraded"


class TestEmoji:
    def test_graded_emoji(self):
        cc = CardCondition(is_graded=True, grading_company="PSA", grade=10.0)
        assert card_condition_emoji(cc) == "💎"

    def test_raw_grades_have_emojis(self):
        for grade in RAW_GRADES:
            assert card_condition_emoji(CardCondition(raw_grade=grade))

    def test_unknown_emoji(self):
        assert card_condition_emoji(CardCondition()) == "❓"


class TestEdgeCases:
    def test_empty_string(self):
        c = detect_card_condition("")
        assert not c.is_known

    def test_no_signal(self):
        c = detect_card_condition("Charizard Base Set 1999")
        assert not c.is_known

    def test_only_psa_no_grade(self):
        # "PSA" alone without a number → no graded match
        c = detect_card_condition("PSA submission pending")
        assert not c.is_graded
