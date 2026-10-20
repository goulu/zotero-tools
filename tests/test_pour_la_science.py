from urllib.parse import parse_qs

import httpx
import pytest

from pour_la_science import (
    AuthenticationError,
    MagazineArticle,
    PourLaScienceClient,
    PourLaScienceConfig,
    parse_issue_page,
)
from pour_la_science.zotero_importer import PourLaScienceZoteroImporter
from pour_la_science.archives import MagazineIssue
from zotero_cleaner.models import ZoteroItem


SSO_URL = (
    "https://connexion.groupepourlascience.fr/?redirect_uri="
    "https%3A%2F%2Fwww.pourlascience.fr%2Flogin"
    "&error_uri=https%3A%2F%2Fconnexion.groupepourlascience.fr"
    "&referer=test&uri_referer=https%3A%2F%2Fwww.pourlascience.fr%2Farchives"
)
LOGIN_FORM = """
<form action="https://sso.qiota.com/api/v1/login" method="post">
  <input type="email" name="email">
  <input type="password" name="password">
  <input type="hidden" name="response_type" value="code">
  <input type="hidden" name="client_id" value="test-client">
  <input type="hidden" name="redirect_uri"
      value="https://www.pourlascience.fr/login">
</form>
"""


def make_mock_client(accept_login):
    archives_visits = 0

    def handler(request):
        nonlocal archives_visits
        if request.url.host == "www.pourlascience.fr":
            if request.url.path == "/archives":
                archives_visits += 1
                if archives_visits > 1 and accept_login:
                    return httpx.Response(
                        200,
                        request=request,
                        text="<html>Archives privées</html>",
                    )
                return httpx.Response(
                    200,
                    request=request,
                    text=(
                        f'<a id="connect_link" href="{SSO_URL}">'
                        "Connexion</a>"
                    ),
                )
            return httpx.Response(
                200,
                request=request,
                text="<html>Archives privées</html>",
            )

        if request.url.host == "connexion.groupepourlascience.fr":
            return httpx.Response(200, request=request, text=LOGIN_FORM)

        if request.url.host == "sso.qiota.com":
            form = parse_qs(request.content.decode())
            assert form["email"] == ["reader@example.test"]
            assert form["password"] == ["test-password"]
            assert form["client_id"] == ["test-client"]
            if accept_login:
                return httpx.Response(
                    302,
                    request=request,
                    headers={"Location": "https://www.pourlascience.fr/login"},
                )
            return httpx.Response(200, request=request, text=LOGIN_FORM)

        raise AssertionError(f"Unexpected request host: {request.url.host}")

    return httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )


def test_login_follows_sso_form_and_reuses_session():
    http_client = make_mock_client(accept_login=True)
    client = PourLaScienceClient(
        PourLaScienceConfig(
            email="reader@example.test",
            password="test-password",
        ),
        http_client=http_client,
    )

    client.login()
    response = client.get_archives_page()

    assert client.is_authenticated
    assert response.text == "<html>Archives privées</html>"
    http_client.close()


def test_login_rejects_credentials_when_sso_returns_login_form():
    http_client = make_mock_client(accept_login=False)
    client = PourLaScienceClient(
        PourLaScienceConfig(
            email="reader@example.test",
            password="test-password",
        ),
        http_client=http_client,
    )

    with pytest.raises(
        AuthenticationError,
        match="credentials may be invalid",
    ):
        client.login()

    assert not client.is_authenticated
    http_client.close()


def test_config_reads_credentials_from_env(monkeypatch):
    monkeypatch.setenv("POURLASCIENCE_EMAIL", "reader@example.test")
    monkeypatch.setenv("POURLASCIENCE_PASSWORD", "test-password")

    config = PourLaScienceConfig.from_env()

    assert config.email == "reader@example.test"
    assert config.password == "test-password"
    assert "test-password" not in repr(config)


def test_get_archives_requires_login():
    client = PourLaScienceClient(
        PourLaScienceConfig(
            email="reader@example.test",
            password="test-password",
        ),
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda request: None),
        ),
    )

    with pytest.raises(AuthenticationError, match=r"Call login\(\)"):
        client.get_archives_page()


def test_parse_issue_page_extracts_articles_and_ignores_recommendations():
    issue_url = (
        "https://www.pourlascience.fr/sd/physique-particules/"
        "pour-la-science-n588-29539.php"
    )
    html = """
    <h1 class="title">Faut-il réinventer la particule ?</h1>
    <section class="block articles">
      <header><h2 class="section-title"><span>À la une</span></h2></header>
      <article class="article miniwide">
        <a href="/sd/physique-particules/article-1.php">
          <h2 class="title"><span class="payant"></span>Article numéro un</h2>
        </a>
        <p class="chapo">Résumé du premier article.</p>
        <span class="author">Alice Auteur</span>
      </article>
      <article class="article miniwide">
        <a href="/sd/physique/article-2.php">
          <h2 class="title">Article numéro deux</h2>
        </a>
        <p class="chapo">Résumé du deuxième article.</p>
        <span class="author">Bob Auteur</span>
        <span class="author">Claire Autrice</span>
      </article>
    </section>
    <section class="block recommendations">
      <article><h2 class="title">Recommandation externe</h2></article>
    </section>
    """

    issue = parse_issue_page(html, issue_url)

    assert issue.title == "Faut-il réinventer la particule ?"
    assert issue.issue_number == "588"
    assert issue.articles == (
        MagazineArticle(
            title="Article numéro un",
            url=(
                "https://www.pourlascience.fr/sd/physique-particules/"
                "article-1.php"
            ),
            authors=("Alice Auteur",),
            abstract="Résumé du premier article.",
            section="À la une",
        ),
        MagazineArticle(
            title="Article numéro deux",
            url="https://www.pourlascience.fr/sd/physique/article-2.php",
            authors=("Bob Auteur", "Claire Autrice"),
            abstract="Résumé du deuxième article.",
            section="À la une",
        ),
    )


def test_import_issue_dry_run_marks_url_and_title_author_duplicates():
    existing = ZoteroItem.model_validate({
        "key": "OLD1",
        "version": 1,
        "data": {
            "key": "OLD1",
            "version": 1,
            "itemType": "journalArticle",
            "title": "L’immunité et les polypes",
            "url": "https://www.pourlascience.fr/article-1.php?source=site",
            "creators": [{
                "creatorType": "author",
                "name": "Alice Auteur",
            }],
        },
    })

    class FakeZoteroClient:
        def iter_all_items(self, top_level):
            assert top_level
            return iter([existing])

        def create_items(self, payloads):
            raise AssertionError("Dry-run must not create Zotero items")

    issue = MagazineIssue(
        url="https://www.pourlascience.fr/sd/x/pour-la-science-n1-100.php",
        title="Numéro test",
        issue_number="1",
        articles=(
            MagazineArticle(
                title="Autre titre",
                url="https://www.pourlascience.fr/article-1.php",
                authors=("Alice Auteur",),
                abstract="Résumé",
            ),
            MagazineArticle(
                title="L'immunite et les polypes",
                url="https://www.pourlascience.fr/article-2.php",
                authors=("Alice Auteur",),
                abstract="Résumé",
            ),
            MagazineArticle(
                title="Nouvel article",
                url="https://www.pourlascience.fr/article-3.php",
                authors=("Bob Auteur",),
                abstract="Résumé neuf",
            ),
        ),
    )

    summary = PourLaScienceZoteroImporter(FakeZoteroClient()).import_issue(
        issue,
        dry_run=True,
    )

    assert summary.duplicates == 2
    assert summary.would_add == 1
    assert summary.errors == 0


def test_import_issue_creates_zotero_journal_articles():
    class FakeZoteroClient:
        def iter_all_items(self, top_level):
            return iter([])

        def create_items(self, payloads):
            self.payloads = payloads
            return {"successful": {"0": {"key": "NEW1"}}}

    zotero = FakeZoteroClient()
    issue = MagazineIssue(
        url="https://www.pourlascience.fr/sd/x/pour-la-science-n588-29539.php",
        title="Issue 588",
        issue_number="588",
        articles=(MagazineArticle(
            title="A new article",
            url="https://www.pourlascience.fr/article.php",
            authors=("Alice Auteur", "Bob Auteur"),
            abstract="Article abstract.",
        ),),
    )

    summary = PourLaScienceZoteroImporter(zotero).import_issue(issue)

    assert summary.added == 1
    assert summary.errors == 0
    assert zotero.payloads[0] == {
        "itemType": "journalArticle",
        "title": "A new article",
        "publicationTitle": "Pour la Science",
        "url": "https://www.pourlascience.fr/article.php",
        "creators": [
            {"creatorType": "author", "name": "Alice Auteur"},
            {"creatorType": "author", "name": "Bob Auteur"},
        ],
        "issue": "588",
        "abstractNote": "Article abstract.",
    }
