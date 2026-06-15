"""Pokemon Emerald walkthrough knowledge — for LLM advisor prompt.

Rule check: project CLAUDE.md forbids "ハードコード: 座標 / map_id 等の
game-specific 数値を コードに埋め込まない (prompt は OK)". This file
encodes domain knowledge purely as prompt text — never imported as
runtime constants into navigation logic. The heuristic / CNN never read
these values; only the LLM sees them through its system prompt.
"""
from __future__ import annotations

EARLY_GAME_KNOWLEDGE = """
POKEMON EMERALD EARLY GAME REFERENCE (use ONLY when uncertain about story progression):

# Maps you should know
- Player starts in BRENDAN'S HOUSE 2F (group=1, num=1) - small bedroom
- Brendan's house 1F (1,0) - living room with Mom
- LITTLEROOT TOWN outside (0,9) - small town with player house, May's house, Birch's lab
- BIRCH'S LAB (1,4) - has Pokeballs and assistant
- May's house 2F (1,9) - has May (rival) sometimes
- ROUTE 101 (0,16) - grass area NORTH of Littleroot, leads to Oldale
- OLDALE TOWN (0,17) - has Pokemon Center and Pokemon Mart
- ROUTE 102 (0,18) - WEST of Oldale, leads to Petalburg

# Early game sequence (MUST be done in order)
1. Wake up in player house 2F, set CLOCK (interact with clock on wall north)
2. Mom calls from downstairs → walk DOWN stairs to 1F
3. Mom dialog (advance with A several times)
4. Open and read notebook on 2F (auto-event)
5. Exit house south door
6. Walk NORTH through Littleroot to Route 101 boundary
7. RIVAL appears with "Um, um, um!" dialog blocking north - keep pressing A many times (~25 presses)
8. Rival leaves after dialog completes
9. Walk into Route 101 GRASS (north)
10. Wild Pokemon attacks Birch → cutscene → Birch gives bag
11. Choose Pokeball from bag (3 starters: Treecko/Torchic/Mudkip - any is fine, just press A on top one)
12. Wild battle - attack until win (press A repeatedly)
13. After win, follow Birch back to lab
14. Get to keep your starter Pokemon → party_count becomes 1

# Tips
- Always advance dialog with A (rarely B for skip)
- If dialog says "Yes/No?" use D-pad to highlight YES, then A
- If stuck on same tile repeatedly, there's likely a CHARACTER or NPC blocking
- A button can interact with things in front of player (signs, NPCs, objects)
- Pressing direction toward an obstacle just faces the player but doesn't move

# Dialog patterns to recognize
- "AAAAGGG flipped open the notebook" = tutorial event, just keep pressing A
- "ADVENTURE RULE NO. X" = walkthrough tutorial, A through pages
- "Um, um, um!" = May/Brendan (rival) blocking - press A many times
- "Quick! Come quickly!" = Mom's emergency, advance dialog then exit house
- "If you go outsid..." = warning sign, dismiss with B or A
- "MOM: ..." = Mom NPC, advance with A
- "Birch ..." = Professor's dialog, advance with A
"""


BUTTON_USAGE = """
Button semantics in Pokemon Emerald (USA):
- A: confirm / advance dialog / interact with NPC or object in front
- B: cancel / skip / quick text / close menu
- Start: open MAIN MENU (POKEMON, BAG, OPTIONS, etc.)
- Select: register item shortcut (usually unused early)
- Up/Down/Left/Right: walk OR change selection in menu
- L/R: not used in normal play

Typical dialog navigation:
- Read dialog → A → next text or close
- Yes/No prompt → Up/Down to highlight → A to confirm
- Menu → D-pad → A confirm → B back
"""


SCREEN_FEATURE_HINTS = """
Visual cues to identify game state:
- White text box at bottom = DIALOG OPEN (use A to advance)
- 2x2 menu grid bottom-right = BATTLE MENU (Fight/Pkmn/Item/Run)
- Right-side menu strip = START MENU OPEN
- All-white screen flash = battle transition or level-up
- Black borders = cutscene playing (just wait or press A)
- Yellow keyboard-like layout = NAME ENTRY (press Start to skip if possible)
"""


def system_prompt_for_advisor() -> str:
    return (
        "You guide a Pokemon Emerald automated agent. Look at the GBA "
        "screenshot (240x160) and the RAM state, then choose 1-6 button "
        "presses to make PROGRESS toward the next early-game milestone.\n\n"
        + EARLY_GAME_KNOWLEDGE
        + "\n"
        + BUTTON_USAGE
        + "\n"
        + SCREEN_FEATURE_HINTS
        + "\n\n"
        "Reply with ONLY a JSON object:\n"
        '{"buttons": ["A","A","Up"], "reason": "<<= 15 words>", '
        '"goal": "<current high-level goal>"}\n'
        "Buttons valid: A B Up Down Left Right Start Select. "
        "Prefer SHORT button lists (2-4 buttons) so you can react each turn. "
        "If past attempts in a direction failed, try a DIFFERENT direction."
    )
