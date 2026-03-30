import sys
import os

sys.path.insert(0, os.path.abspath("."))

import argparse
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent.core import AutonomousAgent
from agent.llm import warmup_model, check_ollama
from rich.console import Console

console = Console()

def main():
    parser = argparse.ArgumentParser(description="Local Autonomous Coding Agent")
    parser.add_argument("task", type=str, help="The task to execute")
    parser.add_argument("--workspace", default="./workspace", help="Path to workspace directory")
    parser.add_argument("--retries", type=int, default=4, help="Maximum number of retries per step")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without executing them")
    parser.add_argument("--fast", action="store_true", help="FAST_MODE: Skipped full test suites to boost raw execution speed")
    
    args = parser.parse_args()
    
    if not args.task.strip():
        console.print("[red]Task description cannot be empty.[/red]")
        sys.exit(1)
        
    console.print("[bold blue]Checking LLM infrastructure locally...[/bold blue]")
    if not check_ollama():
        console.print("[bold red]FATAL: Cannot reach Ollama runtime. Aborting autonomous session.[/bold red]")
        sys.exit(1)
        
    warmup_model()
        
    try:
        agent = AutonomousAgent(
            workspace_dir=args.workspace,
            max_retries=args.retries,
            dry_run=args.dry_run,
            fast_mode=args.fast
        )
        agent.run(args.task)
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Execution interrupted by user. Tearing down execution threads forcefully...[/bold yellow]")
        os._exit(0)
    except Exception as e:
        console.print(f"\n[bold red]Fatal Error:[/bold red] {e}")
        os._exit(1)

if __name__ == "__main__":
    main()
