from src.utils.condition import detect_condition


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
