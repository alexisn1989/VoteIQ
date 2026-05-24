#!/usr/bin/env python3
"""
VoteIQ Admin CLI
Provides access to internal admin agents for code help, deployment, testing, etc.

Usage:
  python3 admin_cli.py 12 "Help me debug this Python error"
  python3 admin_cli.py 12  # Interactive mode
"""
import sys
import os
from enum import Enum
from typing import Optional
from dotenv import load_dotenv
from anthropic import Anthropic

# Load environment
load_dotenv()

class AgentMode(Enum):
    """Admin agent modes"""
    GENERAL_ADMIN = "general_admin"

# Agent configuration
AGENTS = {
    AgentMode.GENERAL_ADMIN: {
        "name": "General Admin Utility",
        "env_var": "VOTEIQ_GENERAL_ADMIN_AGENT_ID",
        "description": "Internal admin assistant for code, scripts, docs, deployment, operations",
        "tags": ["internal", "admin", "operational"],
    }
}

# CLI mode to agent mapping
MODES = {
    "12": "general_admin",
}

class AdminCLI:
    """VoteIQ Admin CLI client"""

    def __init__(self):
        self.client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self._validate_setup()

    def _validate_setup(self):
        """Verify required environment variables are set"""
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("Error: ANTHROPIC_API_KEY not set in environment")

    def get_agent_id(self, mode: str) -> str:
        """Get agent ID for mode"""
        if mode not in MODES:
            raise ValueError(f"Unknown mode: {mode}. Valid modes: {list(MODES.keys())}")

        agent_mode_str = MODES[mode]
        agent_mode = AgentMode(agent_mode_str)
        env_var = AGENTS[agent_mode]["env_var"]
        agent_id = os.getenv(env_var)

        if not agent_id:
            raise RuntimeError(
                f"Error: {env_var} not set in environment\n"
                f"Run: ./deploy-{agent_mode_str}.sh"
            )

        return agent_id

    def call_agent(self, mode: str, query: str) -> str:
        """Call an agent with a query"""
        agent_id = self.get_agent_id(mode)
        agent_mode_str = MODES[mode]
        agent_mode = AgentMode(agent_mode_str)
        agent_name = AGENTS[agent_mode]["name"]

        print(f"\n{'='*60}")
        print(f"Calling: {agent_name} (Mode {mode})")
        print(f"{'='*60}")
        print(f"\nQuery: {query}\n")

        try:
            response = self.client.agents.messages.create(
                agent_id=agent_id,
                messages=[
                    {"role": "user", "content": query}
                ]
            )

            # Extract response text
            result = response.content[0].text if response.content else "No response"
            print(result)
            return result

        except Exception as e:
            print(f"Error calling agent: {e}")
            raise

    def interactive(self, mode: str):
        """Interactive mode - prompt user repeatedly"""
        agent_id = self.get_agent_id(mode)
        agent_mode_str = MODES[mode]
        agent_mode = AgentMode(agent_mode_str)
        agent_name = AGENTS[agent_mode]["name"]

        print(f"\n{'='*60}")
        print(f"VoteIQ Admin CLI - {agent_name}")
        print(f"{'='*60}")
        print(f"Type 'exit' or 'quit' to exit\n")

        while True:
            try:
                query = input("admin> ").strip()

                if not query:
                    continue

                if query.lower() in ["exit", "quit"]:
                    print("\nGoodbye!")
                    break

                self.call_agent(mode, query)
                print()

            except KeyboardInterrupt:
                print("\n\nGoodbye!")
                break
            except Exception as e:
                print(f"Error: {e}")


def main():
    """Main CLI entry point"""
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nAvailable modes:")
        for mode, mode_str in MODES.items():
            agent_mode = AgentMode(mode_str)
            agent_info = AGENTS[agent_mode]
            print(f"  Mode {mode}: {agent_info['name']}")
            print(f"    {agent_info['description']}")
        sys.exit(1)

    mode = sys.argv[1]

    if mode not in MODES:
        print(f"Error: Unknown mode '{mode}'")
        print(f"Valid modes: {list(MODES.keys())}")
        sys.exit(1)

    try:
        cli = AdminCLI()

        # Check if query provided or interactive mode
        if len(sys.argv) > 2:
            # Query provided: run once and exit
            query = " ".join(sys.argv[2:])
            cli.call_agent(mode, query)
        else:
            # Interactive mode
            cli.interactive(mode)

    except RuntimeError as e:
        print(f"\n{e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\nUnexpected error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
