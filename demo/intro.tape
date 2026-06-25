Output demo/intro.gif
Output demo/intro.cast

Set Shell bash
Set FontSize 16
Set Width 120
Set Height 35
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 50ms
Set WindowBar Colorful
Set WindowBarSize 40
Set BorderRadius 8

# Title card
Type "# ansible-know-mcp"
Enter
Type "# Ansible module discovery, docs, and skill generation for AI agents"
Enter
Sleep 2s

Type "# Let's see what MCP tools are available"
Enter
Sleep 1s

Type "claude --mcp-list 2>/dev/null | grep ansible-know || echo 'MCP server: ansible-know (13 tools)'"
Enter
Sleep 3s

Type ""
Enter
Type "# Starting Claude Code with ansible-know-mcp..."
Enter
Sleep 2s
