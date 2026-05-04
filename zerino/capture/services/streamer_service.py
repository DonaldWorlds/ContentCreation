from zerino.db.repositories.streamer_repository import StreamerRepository

class StreamerService:
    """Manages streamer identity and active streamer state"""

    def __init__(self, state):
        self.state = state
        self.streamer_repo = StreamerRepository()

    def ensure_streamer(self, name, platform):
        """Get or create streamer, set as active"""
        streamer = self.streamer_repo.get_streamer_by_name(name)
        if not streamer:
            streamer_id = self.streamer_repo.create_streamer(name, platform)
            print(f"Created streamer: {name} (ID: {streamer_id})")
        else:
            streamer_id = streamer["id"]
            print(f"Loaded streamer: {name} (ID: {streamer_id})")
        
        self.set_active_streamer(streamer_id)
        return streamer_id

    def get_streamer(self, name):
        """Fetch streamer by name"""
        return self.streamer_repo.get_streamer_by_name(name)

    def set_active_streamer(self, streamer_id):
        """Set active streamer in state"""
        self.state["current_streamer_id"] = streamer_id

    def get_active_streamer(self):
        """Get active streamer from state"""
        return self.state.get("current_stremer_id")
    
if __name__ == "__main__":
    # Simulated hsared state 
    state = {
        "current_streamer_id" : None
    }
    service = StreamerService(state)

    print("\n Test Ensure streamer")
    streamer_id = service.ensure_streamer("DonWitherspoon", "Twitch")

    print("\n test get active streamer")
    active_id = service.get_active_streamer()
    print(f"Active streamer ID: {active_id}")

    print("\n Test: Fetch Streamer")
    streamer = service.get_streamer("DonWitherspoon")
    print(f"Stremer from DB: {streamer}")

    print("\n final state")
    print(state)