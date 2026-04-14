"""Seed corpus: ~120 songs across genres, spanning the 'vibe' space.

Goal: cover the full range of (note_density, upbeatness) so nearest-neighbor
queries always have relevant matches.

Format: (title, artist) tuples. iTunes Search will resolve these to trackIds
and previewUrls during seeding.
"""

SEED_SONGS = [
    # ── Ambient / Drone / Minimal (low density, low upbeat) ─────────────
    ("Weightless", "Marconi Union"),
    ("An Ending Ascent", "Brian Eno"),
    ("Avril 14th", "Aphex Twin"),
    ("River Flows in You", "Yiruma"),
    ("Nuvole Bianche", "Ludovico Einaudi"),
    ("Spiegel im Spiegel", "Arvo Pärt"),
    ("Music for Airports 1/1", "Brian Eno"),
    ("Gymnopédie No. 1", "Erik Satie"),
    ("Claire de Lune", "Claude Debussy"),
    ("Experience", "Ludovico Einaudi"),

    # ── Classical / Orchestral (varies) ─────────────────────────────────
    ("Flight of the Bumblebee", "Rimsky-Korsakov"),
    ("Winter Four Seasons", "Vivaldi"),
    ("Toccata and Fugue in D minor", "Bach"),
    ("Für Elise", "Beethoven"),
    ("Rondo Alla Turca", "Mozart"),
    ("Moonlight Sonata", "Beethoven"),
    ("Hungarian Rhapsody No. 2", "Liszt"),
    ("Canon in D", "Pachelbel"),
    ("Swan Lake", "Tchaikovsky"),
    ("1812 Overture", "Tchaikovsky"),
    ("Ride of the Valkyries", "Wagner"),
    ("In the Hall of the Mountain King", "Grieg"),

    # ── Jazz / Lounge (mid density, mid upbeat) ──────────────────────────
    ("Take Five", "Dave Brubeck"),
    ("So What", "Miles Davis"),
    ("Blue in Green", "Miles Davis"),
    ("Autumn Leaves", "Cannonball Adderley"),
    ("Birdland", "Weather Report"),
    ("Sing Sing Sing", "Benny Goodman"),
    ("My Funny Valentine", "Chet Baker"),
    ("Giant Steps", "John Coltrane"),
    ("Round Midnight", "Thelonious Monk"),
    ("Fly Me to the Moon", "Frank Sinatra"),

    # ── Folk / Acoustic / Singer-songwriter ──────────────────────────────
    ("Blackbird", "The Beatles"),
    ("Landslide", "Fleetwood Mac"),
    ("Hallelujah", "Jeff Buckley"),
    ("Tears in Heaven", "Eric Clapton"),
    ("Wish You Were Here", "Pink Floyd"),
    ("Fast Car", "Tracy Chapman"),
    ("The Sound of Silence", "Simon & Garfunkel"),
    ("Heart of Gold", "Neil Young"),
    ("Skinny Love", "Bon Iver"),
    ("Mad World", "Gary Jules"),

    # ── Pop (mid density, mid-high upbeat) ───────────────────────────────
    ("Shape of You", "Ed Sheeran"),
    ("Blinding Lights", "The Weeknd"),
    ("Bad Guy", "Billie Eilish"),
    ("Rolling in the Deep", "Adele"),
    ("Someone Like You", "Adele"),
    ("Firework", "Katy Perry"),
    ("Uptown Funk", "Mark Ronson Bruno Mars"),
    ("Can't Stop the Feeling", "Justin Timberlake"),
    ("Happy", "Pharrell Williams"),
    ("Shake It Off", "Taylor Swift"),
    ("Dynamite", "BTS"),
    ("Watermelon Sugar", "Harry Styles"),
    ("Levitating", "Dua Lipa"),
    ("Havana", "Camila Cabello"),
    ("Anti-Hero", "Taylor Swift"),
    ("As It Was", "Harry Styles"),
    ("Flowers", "Miley Cyrus"),

    # ── Rock (high density, high upbeat) ─────────────────────────────────
    ("Bohemian Rhapsody", "Queen"),
    ("Smells Like Teen Spirit", "Nirvana"),
    ("Stairway to Heaven", "Led Zeppelin"),
    ("Back in Black", "AC/DC"),
    ("Sweet Child O' Mine", "Guns N' Roses"),
    ("Hotel California", "Eagles"),
    ("Don't Stop Believin'", "Journey"),
    ("Livin' on a Prayer", "Bon Jovi"),
    ("Thunderstruck", "AC/DC"),
    ("Welcome to the Jungle", "Guns N' Roses"),
    ("Whole Lotta Love", "Led Zeppelin"),
    ("Another Brick in the Wall Part 2", "Pink Floyd"),
    ("Comfortably Numb", "Pink Floyd"),
    ("Let It Be", "The Beatles"),
    ("Come Together", "The Beatles"),

    # ── Hip-Hop / Rap ────────────────────────────────────────────────────
    ("Lose Yourself", "Eminem"),
    ("Gangsta's Paradise", "Coolio"),
    ("In Da Club", "50 Cent"),
    ("God's Plan", "Drake"),
    ("Sicko Mode", "Travis Scott"),
    ("HUMBLE", "Kendrick Lamar"),
    ("Rap God", "Eminem"),
    ("Juicy", "Notorious B.I.G."),
    ("Empire State of Mind", "Jay-Z Alicia Keys"),
    ("Old Town Road", "Lil Nas X"),
    ("SICKO MODE", "Travis Scott"),

    # ── EDM / Electronic Dance ───────────────────────────────────────────
    ("One More Time", "Daft Punk"),
    ("Levels", "Avicii"),
    ("Titanium", "David Guetta"),
    ("Animals", "Martin Garrix"),
    ("Clarity", "Zedd"),
    ("Summer", "Calvin Harris"),
    ("Don't You Worry Child", "Swedish House Mafia"),
    ("Wake Me Up", "Avicii"),
    ("Lean On", "Major Lazer"),
    ("Strobe", "Deadmau5"),
    ("Around the World", "Daft Punk"),

    # ── Metal (high density, very high upbeat) ───────────────────────────
    ("Enter Sandman", "Metallica"),
    ("Master of Puppets", "Metallica"),
    ("Painkiller", "Judas Priest"),
    ("Raining Blood", "Slayer"),
    ("Chop Suey", "System of a Down"),
    ("Iron Man", "Black Sabbath"),
    ("Ace of Spades", "Motörhead"),
    ("Holy Diver", "Dio"),
    ("Paranoid", "Black Sabbath"),

    # ── R&B / Soul ───────────────────────────────────────────────────────
    ("Superstition", "Stevie Wonder"),
    ("Respect", "Aretha Franklin"),
    ("I Will Always Love You", "Whitney Houston"),
    ("Stay With Me", "Sam Smith"),
    ("Ain't No Sunshine", "Bill Withers"),
    ("Let's Stay Together", "Al Green"),
    ("My Girl", "The Temptations"),
    ("What's Going On", "Marvin Gaye"),

    # ── Country ─────────────────────────────────────────────────────────
    ("Ring of Fire", "Johnny Cash"),
    ("Jolene", "Dolly Parton"),
    ("Friends in Low Places", "Garth Brooks"),
    ("Tennessee Whiskey", "Chris Stapleton"),
    ("Take Me Home Country Roads", "John Denver"),

    # ── Indie / Alternative ──────────────────────────────────────────────
    ("Mr. Brightside", "The Killers"),
    ("Take Me Out", "Franz Ferdinand"),
    ("Dog Days Are Over", "Florence + The Machine"),
    ("Seven Nation Army", "The White Stripes"),
    ("Pumped Up Kicks", "Foster the People"),
    ("Young Folks", "Peter Bjorn and John"),
    ("Float On", "Modest Mouse"),
    ("Somebody That I Used to Know", "Gotye"),
    ("Radioactive", "Imagine Dragons"),
    ("Dreams", "Fleetwood Mac"),
    ("Feel Good Inc", "Gorillaz"),
]
