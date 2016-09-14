import collections
import logging
import Queue
import time
import tweepy
import re

# TODO: Implement feature to time tweets so they retweet randomly after they are found within the 15 minutes (rate frame)

# Logging and Formatter
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logFormatter = logging.Formatter("%(asctime)s %(message)s")

# File log
fileHandler = logging.FileHandler("luckyBot.log", "w")
fileHandler.setFormatter(logFormatter)
logger.addHandler(fileHandler)

# Console log
consoleHandler = logging.StreamHandler()
consoleHandler.setFormatter(logFormatter)
logger.addHandler(consoleHandler)

consumer_key = "Your info here"
consumer_secret = "Your info here"
access_token = "Your info here"
access_token_secret = "Your info here"

auth = tweepy.OAuthHandler(consumer_key, consumer_secret)
auth.set_access_token(access_token, access_token_secret)

api = tweepy.API(auth)

# This is the constant for the number of seconds in the rate window (15 minutes)
RATE_WINDOW_SECONDS = 900

# Used to keep track of the most recent tweet id, which will be used when querying for new tweets. The most recent since
# that id will be added to make sure I am always re-tweeting new tweets.
since_id = 0

# Set of tweets
retweetedTweets = set()

# This is used to make sure that tweets from a user aren't tweeted more than once an hour
userCache = set()
tweetRound = 0

# Open file with tweet information
try:
    with open("tweets.txt", "r+") as tweetFile:
        file_lines = tweetFile.readlines()
        for tweet_id in file_lines:
            retweetedTweets.add(tweet_id.strip())
except IOError:
    logger.info("No retweeted tweets file")
    pass

# A Queue for the users
followedUsers = Queue.Queue(maxsize=1600)

# Open file with information for users we are following
try:
    with open("following.txt", "r+") as followedFile:
        file_lines = followedFile.readlines()
        for user_id in file_lines:
            followedUsers.put(user_id.strip())
except IOError:
    logger.info("No followed users file")
    pass

# HELPER FUNCTIONS

'''
The purpose of this function below is to gather new contest tweet data. It looks for ids of tweets within the 15 minute
rate limited period and returns a list of up to 10 possible candidates to be re-tweeted. A max of 10 allows for the
program to run all day and the tweeting limit to never be reached (A user can only tweet a maximum of 1000 times a day,
the method I use below allows for a max of 960)
'''


def getContestTweets():
    global since_id
    global userCache
    global tweetRound
    newTweets = []
    numQueries = 0

    logger.info("Searching for new contest tweets...\n")
    # Keep querying tweets every second until 10 are found. Only 10 tweets
    # will be tweeted per 15 minutes to allow for tweeting around the clock
    # (Number of tweets per day are limited to 1000, using this methods
    # means the bot will tweet 4*24*10=960 tweets a day)
    while numQueries < 180:
        tweet_package = collections.namedtuple('tweet', ['follow', 'user_id', 'tweet_id', 'tweet_text'])

        # Wait 5 seconds before performing query to get new data. This allows for max possible queries in 15 minutes.
        time.sleep(5)

        # Perform api query for tweets containing words RT to win. (Note: the words may have other words in between)
        try:
            createSearch = api.search("RT to win OR RT to enter OR Retweet to win OR Retweet to enter",
                                      result_type="recent", count=100, since_id=since_id)
        except tweepy.TweepError as tweep_error:
            logger.error(tweep_error)
            pass

        for tweet_data in createSearch:
            # If it is a re-tweeted status then we want to get the original from the tweet
            if hasattr(tweet_data, "retweeted_status"):
                tweet_text = tweet_data.retweeted_status.text
                tweet_id = tweet_data.retweeted_status.id
                user_id = tweet_data.retweeted_status.author.id
            else:
                tweet_text = tweet_data.text
                tweet_id = tweet_data.id
                user_id = tweet_data.author.id

            # If a tweet is found to not be suitable to retweet, this one is skipped.
            if (not checkTweet(tweet_text)):
                continue

            # If follow is found anywhere in the tweet text then we raise the flag to follow. Otherwise set to false.
            if ("follow" in tweet_text or "Follow" in tweet_text or "FOLLOW" in tweet_text):
                follow = True
                # This is the pattern for finding out if there is a derivative of the word follow with an @ sign after.
                follow_pattern = re.compile("^.*(follow|Follow|FOLLOW) \@.*$")

                # If there is an @ in the tweet, that is the user we want to follow, not the user of the tweet. This if
                # statement deals with that case.
                if (follow_pattern.match(tweet_text)):

                    # Get the id of the tweet after the word follow. Remove @ to get the screen name.
                    to_follow = getFollowerID(tweet_text.split())[1:]

                    # Once the screen name of who to follow is found then search the user_mentions of the tweet to get
                    # the id
                    for mention in tweet_data.entities["user_mentions"]:
                        if (mention["screen_name"] == to_follow):
                            user_id = mention["id"]
                            break
            else:
                follow = False

            # If user has been tweeted within the last hour, skip this tweet
            if (user_id in userCache):
                continue
            # If the tweet found is not in the retweeted tweets then add it to the list to retweet
            elif (tweet_id not in retweetedTweets):
                # This if statement ensures the tweet isn't someone re-tweeting the contest and also that it
                # occurs after since_id.
                contest_tweet = tweet_package(follow, user_id, tweet_id, tweet_text)
                newTweets.append(contest_tweet)
                retweetedTweets.add(tweet_id)
                # Use the most recent tweet id as since_id to avoid repeats
                since_id = tweet_id
                # Add to set of users to follow
                userCache.add(user_id)

                logger.info("Added following tweet to list: " + tweet_text)
                logger.info("-------------------------------------------------------------")

                # If 10 tweets have been found and added to the newTweets list then replace the tweet file with
                # all previous tweets plus the new ones that were found. Also increment tweetRound by 1 to
                # represent the passing of a rate window (15 minutes).
                if (len(newTweets) == 10):
                    replaceTweetFile()
                    tweetRound += 1
                    return newTweets


def enterContests(retweet_list):
    # Retweet all in retweet_list
    for contest_tweet in retweet_list:
        try:
            api.retweet(contest_tweet.tweet_id)
        except tweepy.TweepError as tweep_error:
            logger.error(tweep_error)
            pass
        except tweepy.RateLimitError as rate_error:
            logger.error(rate_error)
            pass

        # First see if the followed will fit in the Queue. If not we must un-follow
        # the last in the queue and add in the new individual.
        if (contest_tweet.follow and not api.show_friendship(source_id=api.me().id, target_id=contest_tweet.user_id)[
            0].following):
            try:
                followedUsers.put(contest_tweet.user_id)
            except Queue.Full as queue_error:
                logger.error(queue_error)
                api.destroy_friendship(user_id=followedUsers.get())
                followedUsers.put(contest_tweet.user_id)
                pass

            # Follow the new user
            try:
                api.create_friendship(user_id=contest_tweet.user_id, follow=True)
            except tweepy.TweepError as tweep_error:
                logger.error("Could not add user as follower")
                logger.error(tweep_error)
                pass

    # Rewrite to the queue file at the end of contest entry to ensure it is up to date.
    replaceQueueFile()


def checkTweet(tweet_text):
    will_add = True
    if ("enter here" in tweet_text or "Enter here" in tweet_text):
        will_add = False
    elif ("click" in tweet_text or "Click" in tweet_text):
        will_add = False
    elif ("RT" not in tweet_text and "Retweet" not in tweet_text and "retweet" not in tweet_text):
        will_add = False
    return will_add


def getFollowerID(tweet_tokens):
    index = -1

    try:
        index = tweet_tokens.index("follow")
    except ValueError as ve:
        logger.info("Couldn't find 'follow'")

    try:
        index = tweet_tokens.index("Follow")
    except ValueError as ve:
        logger.info ("Couldn't find 'Follow'")

    try:
        index = tweet_tokens.index("FOLLOW")
    except ValueError as ve:
        logger.info ("Couldn't find 'FOLLOW'")
        logger.warning ("[WARNING] Couldn't find anyone to follow")

    # Return the token right after the @ statement
    return tweet_tokens[index + 1]


def replaceTweetFile():
    global retweetedTweets
    with open("tweets.txt", "w+") as currentTweets:
        for tweet in retweetedTweets:
            currentTweets.write(str(tweet))
            currentTweets.write("\n")


def replaceQueueFile():
    global followedUsers
    temp = Queue.Queue()
    with open("following.txt", "w+") as currentlyFollowing:
        while not followedUsers.empty():
            user = followedUsers.get()
            temp.put(user)
            currentlyFollowing.write(str(user))
            currentlyFollowing.write("\n")
    followedUsers = temp

# MAIN FUNCTION

def main():
    global userCache
    global tweetRound

    while True:
        # Get time from before the contests are retweeted
        time_before = time.time()

        # Gather contest tweets
        newContestTweets = getContestTweets()

        # Re-tweet the contests gathered from the tweets above
        enterContests(newContestTweets)

        # If 15 minutes (the rate window time) has not passed yet then delay by the remaining amount of time
        # (current time - time before the contests are entered)
        time_to_tweet = time.time() - time_before
        if (time_to_tweet < RATE_WINDOW_SECONDS):
            time.sleep(RATE_WINDOW_SECONDS - time_to_tweet)

        # If on the 8th round of tweeting (2 hours has passed), then clear the user cache and reset to 0. This will
        # allow contest tweets from users who were tweeted in that past 2 hours to be tweeted again.
        if (tweetRound == 8):
            userCache.clear()
            tweetRound = 0

# Run main function
main()