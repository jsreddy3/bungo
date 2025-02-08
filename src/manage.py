import click
from src.database import Base, engine
from src.models.database_models import DBSession, DBUser, DBAttempt, DBMessage

@click.group()
def cli():
    pass

@cli.command()
def init_db():
    """Initialize the database schema"""
    click.echo("Creating database tables...")
    Base.metadata.create_all(engine)
    click.echo("Done!")

@cli.command()
def reset_db():
    """Reset the database (WARNING: destroys all data)"""
    if click.confirm('Are you sure you want to reset the database?'):
        click.echo("Dropping all tables...")
        Base.metadata.drop_all(engine)
        click.echo("Creating new tables...")
        Base.metadata.create_all(engine)
        click.echo("Done!")

@cli.command()
def migrate_to_wldd():
    """Migrate database from user_id to wldd_id"""
    if click.confirm('Are you sure you want to migrate the database to wldd_id?'):
        click.echo("Starting migration...")
        from migrations.migrate_to_wldd_id import migrate
        migrate()
        click.echo("Migration complete!")

if __name__ == '__main__':
    cli() 