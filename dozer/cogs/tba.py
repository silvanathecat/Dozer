"""A series of commands that talk to The Blue Alliance."""
import datetime
import itertools
import json

from pprint import pformat
from urllib.parse import quote as urlquote, urljoin

import discord
from discord.ext.commands import BadArgument
import googlemaps
import async_timeout
import aiotba
from geopy.geocoders import Nominatim

from ._utils import *


class TBA(Cog):
    """Commands that talk to The Blue Alliance"""
    def __init__(self, bot):
        super().__init__(bot)
        tba_config = bot.config['tba']
        self.gmaps_key = bot.config['gmaps_key']
        self.session = aiotba.TBASession(tba_config['key'], self.bot.http._session)
        # self.parser = tbapi.TBAParser(tba_config['key'], cache=False)

    @group(invoke_without_command=True)
    async def tba(self, ctx, team_num: int):
        """
        Get FRC-related information from The Blue Alliance.
        If no subcommand is specified, the `team` subcommand is inferred, and the argument is taken as a team number.
        """
        await self.team.callback(self, ctx, team_num)

    tba.example_usage = """
    `{prefix}tba 5052` - show information on team 5052, the RoboLobos
    """

    @tba.command()
    @bot_has_permissions(embed_links=True)
    async def team(self, ctx, team_num: int):
        """Get information on an FRC team by number."""
        try:
            team_data = await self.session.team(team_num)
        except aiotba.http.AioTBAError:
            raise BadArgument(f"Couldn't find data for team {team_num}.")
        if team_data.city is None:
            raise BadArgument("team {} exists, but has no information!".format(team_num))

        try:
            team_district_data = await self.session.team_districts(team_num)
            if team_district_data:
                team_district = max(team_district_data, key=lambda d: d.year)
        except aiotba.http.AioTBAError:
            team_district_data = None

        e = discord.Embed(color=discord.Color.blurple(),
                          title='FIRST® Robotics Competition Team {}'.format(team_num),
                          url='https://www.thebluealliance.com/team/{}'.format(team_num))
        e.set_thumbnail(url='https://frcavatars.herokuapp.com/get_image?team={}'.format(team_num))
        e.add_field(name='Name', value=team_data.nickname)
        e.add_field(name='Rookie Year', value=team_data.rookie_year)
        e.add_field(name='Location',
                    value='{0.city}, {0.state_prov} {0.postal_code}, {0.country}'.format(team_data))
        e.add_field(name='Website', value=team_data.website)
        if team_district_data:
            e.add_field(name='District', value=f"{team_district.display_name} [{team_district.abbreviation.upper()}]")
        e.add_field(name='Championship', value=team_data.home_championship[max(team_data.home_championship.keys())])
        e.set_footer(text='Triggered by ' + ctx.author.display_name)
        await ctx.send(embed=e)

    team.example_usage = """
    `{prefix}tba team 4131` - show information on team 4131, the Iron Patriots
    """

    @tba.command()
    @bot_has_permissions(embed_links=True)
    async def eventsfor(self, ctx, team_num: int, year: int = None):
        """Get the events a team is registered for a given year. Defaults to current (or upcoming) year."""
        try:
            events = await self.session.team_events(team_num, year=year)
            # will fall back to the current year
            year = year or (await self.session.status()).current_season
        except aiotba.http.AioTBAError:
            raise BadArgument(f"Couldn't find matching data!")

        if not events:
            raise BadArgument(f"Couldn't find matching data!")
            return

        e = discord.Embed(color=discord.Color.blurple())

        e.add_field(name=f"Registered events for FRC Team {team_num} in {year}:",
                    value="\n".join(i.name for i in events))
        await ctx.send(embed=e)

    eventsfor.example_usage = """
    `{prefix}tba eventsfor 1533` - show the currently registered events for team 1533, Triple Strange
    """

    @tba.command()
    @bot_has_permissions(embed_links=True)
    async def media(self, ctx, team_num: int, year: int = None):
        """Get media of a team for a given year. Defaults to current year."""
        if year is None:
            year = datetime.datetime.today().year
        try:
            team_media = await self.session.team_media(team_num, year)

            pages = []
            base = f"FRC Team {team_num} {year} Media: "
            for media in team_media:

                if media.type == "youtube":
                    pages.append(f"**{base} YouTube** \nhttps://youtu.be/{media.foreign_key}")
                    continue
                else:
                    name, url, img_url = {
                        "cdphotothread": (
                            "Chief Delphi",
                            "https://www.chiefdelphi.com/media/photos/{foreign_key}",
                            "https://www.chiefdelphi.com/media/img/{image_partial}"
                        ),
                        "imgur": (
                            "Imgur",
                            "https://imgur.com/{foreign_key}",
                            "https://i.imgur.com/{foreign_key}.png"
                        ),
                        "instagram-image": (
                            "instagram",
                            "https://www.instagram.com/p/{foreign_key}",
                            "https://www.instagram.com/p/{foreign_key}/media"
                        ),
                        "grabcad": (
                            "GrabCAD",
                            "https://grabcad.com/library/{foreign_key}",
                            "{model_image}"
                        )
                    }.get(media.type, (None, None, None))
                    if name is None:
                        # print("Whack media", media.__dict__, "unprocessed")
                        continue
                    media.details['foreign_key'] = media.foreign_key
                    page = discord.Embed(title=base + name, url=url.format(**media.details))
                    page.set_image(url=img_url.format(**media.details))
                    pages.append(page)

            if len(pages):
                await paginate(ctx, pages)
            else:
                await ctx.send(f"No media for team {team_num} found in {year}!")

        except aiotba.http.AioTBAError:
            raise BadArgument("Couldn't find data for team {}".format(team_num))

    media.example_usage = """
    `{prefix}`tba media 971 2016` - show available media from team 971 Spartan Robotics in 2016
    """

    @tba.command()
    @bot_has_permissions(embed_links=True)
    async def awards(self, ctx, team_num: int, year: int = None):
        """Gets a list of awards the specified team has won during a year. """
        try:
            awards_data = await self.session.team_awards(team_num, year=year)
            events_data = await self.session.team_events(team_num, year=year)
            event_key_map = {event.key: event for event in events_data}
        except aiotba.http.AioTBAError:
            raise BadArgument("Couldn't find data for team {}".format(team_num))

        pages = []
        for award_year, awards in itertools.groupby(awards_data, lambda a: a.year):
            e = discord.Embed(title=f"Awards for FRC Team {team_num} in {award_year}:", color=discord.Color.blurple())
            for event_key, event_awards in itertools.groupby(list(awards), lambda a: a.event_key):
                event = event_key_map[event_key]
                e.add_field(name=f"{event.name} [{event_key}]",
                            value="\n".join(map(lambda a: a.name, event_awards)), inline=False)

            pages.append(e)
        if len(pages) > 1:
            await paginate(ctx, pages, start=-1)
        elif len(pages) == 1:
            await ctx.send(embed=pages[0])
        else:
            await ctx.send(f"This team hasn't won any awards in {year}"
                           if year is not None else "This team hasn't won any awards...yet.")

    awards.example_usage = """
    `{prefix}`tba awards 1114` - list all the awards team 1114 Simbotics has ever gotten.
    """

    @tba.command()
    async def raw(self, ctx, team_num: int):
        """
        Get raw TBA API output for a team.
        This command is really only useful for development.
        """
        try:
            team_data = await self.session.team(team_num)
            e = discord.Embed(color=discord.Color.blurple())
            e.set_author(name='FIRST® Robotics Competition Team {}'.format(team_num),
                         url='https://www.thebluealliance.com/team/{}'.format(team_num),
                         icon_url='https://frcavatars.herokuapp.com/get_image?team={}'.format(team_num))
            e.add_field(name='Raw Data', value=pformat(team_data.__dict__))
            e.set_footer(text='Triggered by ' + ctx.author.display_name)
            await ctx.send(embed=e)
        except aiotba.http.AioTBAError:
            raise BadArgument('Team {} does not exist.'.format(team_num))

    raw.example_usage = """
    `{prefix}tba raw 4150` - show raw information on team 4150, FRobotics
    """

    class TeamData:
        """polyfill data class used to abstract team location data from frc/ftc"""
        country: str
        state_prov: str
        city: str

    @command()
    @bot_has_permissions(embed_links=True)
    async def weather(self, ctx, team_num: int, team_program="frc"):
        """Finds the current weather for a given team."""

        if team_program.lower() == "frc":
            try:
                td = await self.session.team(team_num)
            except aiotba.http.AioTBAError:
                raise BadArgument('Team {} does not exist.'.format(team_num))
        elif team_program.lower() == "ftc":
            res = json.loads(await self.bot.cogs['TOA'].parser.req("team/" + str(team_num)))
            if not res:
                raise BadArgument('Team {} does not exist.'.format(team_num))
            td_dict = res[0]
            td = self.TeamData()
            td.__dict__.update(td_dict)
        else:
            raise BadArgument('`team_program` should be one of [`frc`, `ftc`]')

        units = 'm'
        if td.country == "USA":
            td.country = "United States of America"
            units = 'u'
        e = discord.Embed(title=f"Current weather for {team_program.upper()} Team {team_num}:")
        e.set_image(url="https://wttr.in/" + urlquote(f"{td.city}+{td.state_prov}+{td.country}_0_{units}.png"))
        e.set_footer(text="Powered by wttr.in and sometimes TBA")
        await ctx.send(embed=e)

    weather.example_usage = """
    `{prefix}weather 5052` - show the current weather for FRC team 5052, The RoboLobos
    `{prefix}weather 15470 ftc` - show the current weather for FTC team 15470 
    """

    @command()
    async def timezone(self, ctx, team_num: int, team_program="frc"):
        """
        Get the timezone of a team based on the team number.
        """

        if team_program.lower() == "frc":
            try:
                team_data = await self.session.team(team_num)
            except aiotba.http.AioTBAError:
                raise BadArgument('Team {} does not exist.'.format(team_num))
            if team_data.city is None:
                raise BadArgument("team {} exists, but does not have sufficient information!".format(team_num))

        elif team_program.lower() == "ftc":
            res = json.loads(await self.bot.cogs['TOA'].parser.req("team/" + str(team_num)))
            if not res:
                raise BadArgument('Team {} does not exist.'.format(team_num))
            team_data_dict = res[0]
            team_data = self.TeamData()
            team_data.__dict__.update(team_data_dict)
        else:
            raise BadArgument('`team_program` should be one of [`frc`, `ftc`]')

        location = '{0.city}, {0.state_prov} {0.country}'.format(team_data)
        gmaps = googlemaps.Client(key=self.gmaps_key)
        geolocator = Nominatim(user_agent="Dozer Discord Bot")
        geolocation = geolocator.geocode(location)

        if self.gmaps_key and not self.bot.config['tz_url']:
            timezone = gmaps.timezone(location="{}, {}".format(geolocation.latitude, geolocation.longitude),
                                      language="json")
            utc_offset = float(timezone["rawOffset"]) / 3600
            if timezone["dstOffset"] == 3600:
                utc_offset += 1
            tzname = timezone["timeZoneName"]
        else:
            async with async_timeout.timeout(5) as _, self.bot.http_session.get(urljoin(
                    self.bot.config['tz_url'], str(geolocation.latitude) + "/" + str(geolocation.longitude))) as r:
                r.raise_for_status()
                data = await r.json()
                utc_offset = data["utc_offset"]
                tzname = '`' + data["tz"] + '`'

        current_time = datetime.datetime.utcnow() + datetime.timedelta(hours=utc_offset)

        await ctx.send(f"Timezone: {tzname} UTC{utc_offset:+g}\n"+
                       current_time.strftime("Current Time: %I:%M:%S %p (%H:%M:%S)"))

    timezone.example_usage = """
    `{prefix}timezone 5052` - show the local time of FRC team 5052, The RoboLobos
    `{prefix}timezone 15470 ftc` - show the local time of FTC team 15470
    """


def setup(bot):
    """Adds the TBA cog to the bot"""
    bot.add_cog(TBA(bot))
