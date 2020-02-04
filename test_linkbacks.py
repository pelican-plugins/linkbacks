import os
from tempfile import TemporaryDirectory

from pelican.generators import ArticlesGenerator
from pelican.tests.support import get_settings, unittest

from linkbacks import process_all_articles_linkbacks


CUR_DIR = os.path.dirname(__file__)
TEST_CONTENT_DIR = os.path.join(CUR_DIR, 'test_content')


class LinkbackPosterTest(unittest.TestCase):

    def test_process_all_articles_linkbacks(self):
        with TemporaryDirectory() as tmpdirname:
            process_all_articles_linkbacks([build_article_generator(
                get_settings(filenames={}), TEST_CONTENT_DIR, tmpdirname)])


def build_article_generator(settings, content_path, output_path=None):
    context = settings.copy()
    context['generated_content'] = dict()
    context['static_links'] = set()
    article_generator = ArticlesGenerator(
        context=context, settings=settings,
        path=content_path, theme=settings['THEME'], output_path=output_path)
    article_generator.generate_context()
    return article_generator
