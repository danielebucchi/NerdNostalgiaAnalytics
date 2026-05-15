from src.collectors.retrogaming import retrogamingshop_search_url, backingame_search_url


class TestSearchUrls:
    def test_retrogamingshop_url(self):
        url = retrogamingshop_search_url("metroid fusion")
        assert "retrogamingshop.it" in url
        assert "metroid" in url

    def test_backingame_url(self):
        url = backingame_search_url("zelda ocarina")
        assert "backingame.fr" in url
        assert "zelda" in url

    def test_special_chars_encoded(self):
        url = retrogamingshop_search_url("mario & luigi")
        assert "retrogamingshop.it" in url
        assert "%26" in url or "&" not in url.split("?")[1]
