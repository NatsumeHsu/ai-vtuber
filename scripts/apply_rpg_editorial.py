"""Bind curated narrative copy to exact upstream source strings."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vtuber.rpg.idleology.core.first_use import TUTORIALS
from vtuber.rpg.idleology.core.character.tutorial import _SCENES
from vtuber.rpg.idleology.core.npc_voices import _VOICES


def main():
    folder = ROOT / 'vtuber/rpg/locales'
    target = folder / 'editorial.json'
    translations = json.loads(target.read_text(encoding='utf-8'))
    tutorials = json.loads((folder / 'tutorials_zh_TW.json').read_text(encoding='utf-8'))
    for key, value in tutorials.items():
        translations[TUTORIALS[key]['description']] = value['description']
        for source, text in zip(TUTORIALS[key].get('tips', []), value.get('tips', [])):
            translations[source] = text
    tips = json.loads((folder / 'tips_zh_TW.json').read_text(encoding='utf-8'))
    for key, lines in tips.items():
        if len(lines) != len(TUTORIALS[key]['tips']):
            raise ValueError(f'Tutorial tip count mismatch: {key}')
        translations.update(zip(TUTORIALS[key]['tips'], lines))
    translations.update(json.loads((folder / 'terms_editorial.json').read_text(encoding='utf-8')))
    scenes = json.loads((folder / 'scenes_zh_TW.json').read_text(encoding='utf-8'))
    for source, translated in zip(_SCENES, scenes):
        translations[source['title']] = translated['title']
        translations[source['text']] = translated['text']
    voices = json.loads((folder / 'npc_zh_TW.json').read_text(encoding='utf-8'))
    if set(voices) != set(_VOICES):
        raise ValueError('NPC groups do not match the pinned upstream version')
    for key, lines in voices.items():
        if len(lines) != len(_VOICES[key]):
            raise ValueError(f'NPC line count mismatch: {key}')
        translations.update(zip(_VOICES[key], lines))
    source_stories = json.loads((ROOT / 'vtuber/rpg/idleology/assets/partners/affinity_stories.json').read_text(encoding='utf-8'))
    stories = json.loads((folder / 'stories_zh_TW.json').read_text(encoding='utf-8'))
    if set(stories) != set(source_stories):
        raise ValueError('Story characters do not match upstream')
    for character, chapters in source_stories.items():
        if set(chapters) != set(stories[character]):
            raise ValueError('Story chapters do not match upstream')
        for chapter, source in chapters.items():
            for key in ('title', 'text'):
                translations[source[key]] = stories[character][chapter][key]
    target.write_text(json.dumps(translations, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Editorial overrides: {len(translations)}')


if __name__ == '__main__':
    main()
