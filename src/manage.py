import click
from src.database import Base, engine
from src.models.database_models import DBSession, DBUser, DBAttempt, DBMessage

@click.group()
def cli():
    pass

@cli.command()
def init_db():
    """Initialize the database schema"""
    click.echo("Starting database initialization...")  # Debug log
    try:
        Base.metadata.create_all(engine)
        click.echo("Successfully created all tables!")
    except Exception as e:
        click.echo(f"Error creating tables: {str(e)}")
        raise

@cli.command()
def reset_db():
    """Reset the database (WARNING: destroys all data)"""
    if click.confirm('Are you sure you want to reset the database?'):
        click.echo("Dropping all tables...")
        Base.metadata.drop_all(engine)
        click.echo("Creating new tables...")
        Base.metadata.create_all(engine)
        click.echo("Done!")

if __name__ == '__main__':
    cli() 