#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
Usage: backup_twitter.py --credentials=<CREDENTIALS_FILE> --dir=<DIR>
       backup_twitter.py -h --help

Options:
    --credentials=<CREDENTIALS_FILE>
        Path to an HCL or JSON file containing Twitter API credentials.
        The file should be a dict with four keys:
        * consumer_key
        * consumer_secret
        * access_token
        * access_token_secret

    --dir=<DIR>
        Directory to store the backed-up tweet data.

"""

from collections.abc import MutableMapping
import json
import os
import shutil

import attr
import docopt
import hcl
import tenacity
import tweepy


@attr.s
class TwitterCredentials:
    consumer_key = attr.ib()
    consumer_secret = attr.ib(repr=False)
    access_token = attr.ib()
    access_token_secret = attr.ib(repr=False)

    @classmethod
    def from_path(cls, path):
        data = hcl.load(open(path))
        return cls(**data)


@attr.s
class TweetStore(MutableMapping):
    path = attr.ib()
    tweet_data = attr.ib()

    @classmethod
    def from_path(cls, path):
        os.makedirs(path, exist_ok=True)

        try:
            tweet_data = json.load(open(os.path.join(path, 'tweets.json')))
        except FileNotFoundError:
            tweet_data = {}

        return cls(path=path, tweet_data=tweet_data)

    def save(self, tweet, reindex=False):
        if reindex or (str(tweet.id) not in self):
            self[str(tweet.id)] = tweet

    def __getitem__(self, tweet_id):
        return self.tweet_data[tweet_id]

    def __setitem__(self, tweet_id, tweet):
        path = os.path.join(self.path, tweet_id[:2], tweet_id)
        os.makedirs(path, exist_ok=True)

        json_data = json.dumps(tweet._json, indent=2, sort_keys=True)
        with open(os.path.join(path, 'info.json'), 'w') as outfile:
            outfile.write(json_data)

        assert not hasattr(tweet, 'extended_entities')

        self.tweet_data[tweet_id] = os.path.relpath(path, start=self.path)
        json_data = json.dumps(self.tweet_data, indent=2, sort_keys=True)
        with open(os.path.join(self.path, 'tweets.json'), 'w') as outfile:
            outfile.write(json_data)

    def __delitem__(self, tweet_id):
        raise NotImplementedError

    def __iter__(self):
        return iter(self.tweet_data)

    def __len__(self):
        return len(self.tweet_data)


def setup_api(credentials):
    """Authorise the use of the Twitter API.

    :param credentials: An instance of TwitterCredentials.

    """
    auth = tweepy.OAuthHandler(
        consumer_key=credentials.consumer_key,
        consumer_secret=credentials.consumer_secret)
    auth.set_access_token(
        key=credentials.access_token,
        secret=credentials.access_token_secret
    )
    return tweepy.API(auth)


def get_tweets(method, *args, **kwargs):
    """Generates tweets for a given API method.

    :param method: The tweepy API method to call.
    :param args: args to pass to the API method.
    :param kwargs: kwargs to pass to the API method.

    """
    # We always want the extended mode: this includes the full text of tweets
    # that are >140 characters.
    # https://dev.twitter.com/overview/api/upcoming-changes-to-tweets
    kwargs['tweet_mode'] = 'extended'

    @tenacity.retry(wait=tenacity.wait_exponential(multiplier=1, max=10))
    def _get_next_tweets():
        return method(*args, **kwargs)

    # Keep going until we've exhausted all the tweets from the API, or
    # something else causes us to break.
    while True:
        new_tweets = _get_next_tweets()
        yield from new_tweets

        # What is the earliest ID of the tweets we've seen?  We'll want
        # to get everything up to that point on the next call.
        earliest_id = min(tweet.id for tweet in new_tweets)
        kwargs['max_id'] = earliest_id - 1


if __name__ == '__main__':
    args = docopt.docopt(__doc__)

    credentials = TwitterCredentials.from_path(args['--credentials'])
    api = setup_api(credentials=credentials)

    store = TweetStore.from_path(path=args['--dir'])

    for tweet in get_tweets(api.user_timeline):
        store.save(tweet)
