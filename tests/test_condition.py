from src.utils.condition import (
    VG_BOX_ONLY,
    VG_CIB,
    VG_GRADED,
    VG_LOOSE,
    VG_MANUAL_ONLY,
    VG_MISSING_MANUAL,
    VG_SEALED,
    VG_UNKNOWN,
    detect_condition,
    detect_videogame_condition,
)


class TestConditionDetection:
    def test_loose_italian(self):
        assert detect_condition("Metroid Fusion solo cartuccia GBA") == "Ungraded"
        assert detect_condition("Pokemon Emerald senza scatola") == "Ungraded"

    def test_loose_english(self):
        assert detect_condition("Charizard card only loose") == "Ungraded"
        assert detect_condition("Super Mario 64 cart only") == "Ungraded"

    def test_cib_italian(self):
        assert detect_condition("Pokemon Emerald completo con scatola") == "Complete in Box"
        assert detect_condition("Zelda boxato originale") == "Complete in Box"

    def test_cib_english(self):
        assert detect_condition("Metroid Fusion CIB complete") == "Complete in Box"

    def test_sealed(self):
        assert detect_condition("Pokemon Emerald sigillato factory sealed") == "New/Sealed"
        assert detect_condition("Charizard blister nuovo") == "New/Sealed"

    def test_graded(self):
        assert detect_condition("Charizard PSA 10 gem mint") == "Graded (PSA)"
        assert detect_condition("Mewtwo BGS 9.5") == "Graded (PSA)"

    def test_unknown(self):
        assert detect_condition("Charizard base set") == "Unknown"
        assert detect_condition("Pokemon game") == "Unknown"

    def test_graded_takes_priority(self):
        # "nuovo" could be sealed, but "PSA" is more specific
        assert detect_condition("Charizard nuovo PSA 10") == "Graded (PSA)"

    def test_french(self):
        assert detect_condition("Dracaufeu sans boite") == "Ungraded"
        assert detect_condition("Dracaufeu complet avec boite") == "Complete in Box"

    def test_german(self):
        assert detect_condition("Glurak ohne OVP lose") == "Ungraded"
        assert detect_condition("Glurak komplett mit OVP") == "Complete in Box"


class TestVideogameConditionDetailed:
    """The richer 7-bucket scheme: Graded / Sealed / CIB / Missing Manual /
    Loose (disc only) / Box Only / Manual Only."""

    # --- Missing Manual ---
    def test_missing_manual_italian(self):
        assert detect_condition("Pokemon Smeraldo con scatola senza manuale") == VG_MISSING_MANUAL
        assert detect_condition("Zelda completo manca il manuale") == VG_MISSING_MANUAL
        assert detect_condition("Mario Sunshine mancante manuale") == VG_MISSING_MANUAL

    def test_missing_manual_english(self):
        assert detect_condition("Metroid Fusion no manual") == VG_MISSING_MANUAL
        assert detect_condition("Halo 3 missing manual but boxed") == VG_MISSING_MANUAL

    def test_missing_manual_beats_cib(self):
        # "completo" alone says CIB but "senza manuale" downgrades it
        assert detect_condition("gioco completo senza manuale") == VG_MISSING_MANUAL

    # --- Box Only ---
    def test_box_only_italian(self):
        assert detect_condition("Pokemon Rosso solo custodia") == VG_BOX_ONLY
        assert detect_condition("Mario 64 scatola vuota") == VG_BOX_ONLY
        assert detect_condition("solo scatola Zelda OOT") == VG_BOX_ONLY

    def test_box_only_english(self):
        assert detect_condition("Zelda Ocarina of Time box only") == VG_BOX_ONLY
        assert detect_condition("case only Pokemon Yellow") == VG_BOX_ONLY
        assert detect_condition("empty box Halo CE") == VG_BOX_ONLY

    def test_box_only_beats_cib(self):
        # "scatola originale" alone says CIB but "solo custodia" wins
        assert detect_condition("scatola originale solo custodia") == VG_BOX_ONLY

    # --- Manual Only ---
    def test_manual_only(self):
        assert detect_condition("solo manuale Pokemon Crystal") == VG_MANUAL_ONLY
        assert detect_condition("manual only Final Fantasy VII") == VG_MANUAL_ONLY
        assert detect_condition("solo libretto istruzioni") == VG_MANUAL_ONLY

    # --- Loose / disc-only ---
    def test_disc_only_italian(self):
        assert detect_condition("Crash Bandicoot solo disco") == VG_LOOSE
        assert detect_condition("Pokemon Smeraldo solo cartuccia") == VG_LOOSE

    def test_disc_only_english(self):
        assert detect_condition("FFVII disc only") == VG_LOOSE
        assert detect_condition("Pokemon Yellow cart only") == VG_LOOSE

    # --- Graded (videogames: WATA / VGA / CGC) ---
    def test_graded_wata(self):
        assert detect_condition("Super Mario Bros WATA 9.8 A++") == VG_GRADED
        cc = detect_videogame_condition("Super Mario Bros WATA 9.8 A++")
        assert cc.is_graded
        assert cc.grading_company == "WATA"
        assert cc.grade == 9.8

    def test_graded_vga(self):
        cc = detect_videogame_condition("Zelda OOT VGA 85")
        assert cc.is_graded
        assert cc.grading_company == "VGA"
        assert cc.grade == 85.0

    def test_graded_cgc(self):
        cc = detect_videogame_condition("Pokemon Red CGC 9.6")
        assert cc.is_graded
        assert cc.grading_company == "CGC"

    # --- Sealed ---
    def test_sealed_videogame(self):
        assert detect_condition("Pokemon Smeraldo factory sealed") == VG_SEALED

    # --- Dataclass quality scoring ---
    def test_quality_ordering(self):
        graded = detect_videogame_condition("Mario WATA 9.8")
        sealed = detect_videogame_condition("Pokemon factory sealed")
        cib = detect_videogame_condition("Zelda completo")
        no_manual = detect_videogame_condition("Halo senza manuale")
        loose = detect_videogame_condition("FFVII disc only")
        box_only = detect_videogame_condition("solo custodia")
        unknown = detect_videogame_condition("just a game")
        assert graded.quality_score > sealed.quality_score > cib.quality_score
        assert cib.quality_score > no_manual.quality_score > loose.quality_score
        assert loose.quality_score > box_only.quality_score
        assert unknown.quality_score == 0
        assert unknown.is_known is False

    def test_display_label_graded(self):
        cc = detect_videogame_condition("Mario WATA 9.8")
        assert cc.display == "WATA 9.8"
        # PriceCharting label collapses to a single bucket
        assert cc.label == "Graded (PSA)"

    def test_display_label_buckets(self):
        assert detect_videogame_condition("solo custodia").display == "Box Only"
        assert detect_videogame_condition("senza manuale").display == "Missing Manual"


class TestVideogameConditionEdgeCases:
    def test_empty_text(self):
        assert detect_condition("") == VG_UNKNOWN
        assert detect_videogame_condition("").is_known is False

    def test_no_signal(self):
        # Bare title with no condition signal at all
        assert detect_condition("Pokemon Crystal Game Boy") == VG_UNKNOWN

    def test_case_insensitive_new(self):
        assert detect_condition("SOLO CUSTODIA") == VG_BOX_ONLY
        assert detect_condition("SENZA MANUALE") == VG_MISSING_MANUAL
