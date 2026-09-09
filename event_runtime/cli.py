import typer

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main():
    """Run local, review-gated agent workflows."""


@app.command()
def version():
    """Print the runtime version."""
    typer.echo("0.1.0")
