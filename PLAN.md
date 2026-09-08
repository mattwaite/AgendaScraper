# Flatwater Free Press Agenda Scraper, API version

The end goal of this project is to have a set of scrapers that obtain meeting dates and information across multiple governments and updates a central dataset through an API. The scrapers need to check for existing data and prevent duplicates from appearing in the dataset. Each government will get it's own scraper. 

The API that these scrapers will service is for a website that handles giving assignments to reporters to go cover government meetings. That website will show a list of current meetings and who is assigned to them. This project forms the base layer of data for that application. 

### Resources

In this folder is some prior work that scrapes meetings from the Lincoln City Council into a csv. It works well and should provide a good guide as to what will come next. 

Here's a link to some scraper documentation: https://necivicnewsroom.up.railway.app/docs/scraper-api-guide

The API key lives in `.env` (gitignored) as `PLATFORM_API_KEY`. See `.env.example` for the variable names.

This email from Ben VanKat, the developer of the application, will give some context: 

At last check, Matt Waite asked about an API and asked a couple related questions. Let's start there with my answers:
Claude and I built an API to get meetings into the system. This would be an additional option beyond the previous CSV upload, which remains in place. Below is a link to the API reference, and an API key so Matt can poke around and see if it's functional. 
As Matt suggested, the API gives us much better options for managing dupes, but it still might need work. Obviously this is an important issue — avoiding extra admin work is key. I'm happy to adjust fields/forms/records as needed to accommodate. 
Leah gave me a starting list of meeting agencies for Omaha and Lincoln, with priority agencies listed first. Leah, please read Matt's question — "What constitutes a meeting you want to know about?" — and help guide us to a decision. In my mind, there would be a "Lincoln City Council scraper" and a "Lincoln Planning Commission scraper" and an "Omaha Port Authority scraper" ... a scraper for each meeting type per entity. We'd spin up new scrapers when you wanted a new type of meetings. But all of this is still open for discussion. Here's Leah's agency list. Tell me how I can be most helpful here.
LINCOLN: Lincoln City Council, Lancaster County Commissioners, Lincoln Public School Board of Ed, Lincoln-Lancaster County Planning Commission
OMAHA: Omaha City Council, Douglas County Commissioners, Sarpy County Commissioners, Omaha School Board, Omaha Streetcar Authority, Omaha Port Authority, OPPD Board, Downton Business Improvement District, Blackstone BID 

